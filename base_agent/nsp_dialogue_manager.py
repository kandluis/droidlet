"""
Copyright (c) Facebook, Inc. and its affiliates.
"""
import json
import logging
import os
import re
import spacy
import ast
import pkg_resources
from typing import Tuple, Dict, Optional
from glob import glob
import csv
from jsonschema import validate, exceptions, RefResolver
from time import time
import sentry_sdk

import preprocess

from base_agent.memory_nodes import ProgramNode
from base_agent.dialogue_manager import DialogueManager
from base_agent.dialogue_objects import (
    BotGreet,
    DialogueObject,
    Say,
    coref_resolve,
    process_spans_and_remove_fixed_value,
)
from craftassist.test.validate_json import JSONValidator
from dlevent import sio

from base_util import hash_user

spacy_model = spacy.load("en_core_web_sm")

class NSPDialogueManager(DialogueManager):
    """Dialogue manager driven by neural network.

    Attributes:
        dialogue_objects (dict): Dictionary specifying the DialogueObject
            class for each dialogue type. Keys are dialogue types. Values are
            corresponding class names. Example dialogue objects:
            {'interpreter': MCInterpreter,
            'get_memory': GetMemoryHandler,
            'put_memory': ...
            }
        safety_words (List[str]): Set of blacklisted words or phrases. Commands
            containing these are automatically filtered out.
        botGreetings (dict): Different types of greetings that trigger
            scripted responses. Example:
            { "hello": ["hi bot", "hello"] }
        model (TTADBertModel): Semantic Parsing model that takes text as
            input and outputs a logical form.
            To use a new model here, ensure that the subfolder directory structure
            mirrors the current model/dataset directories.
            See :class:`TTADBertModel`.
        ground_truth_actions (dict): A key-value with ground truth logical forms.
            These will be queried first (via exact string match), before running the model.
        dialogue_object_parameters (dict): Set the parameters for dialogue objects.
            Sets the agent, memory and dialogue stack.

    Args:
        agent: a droidlet agent, eg. ``CraftAssistAgent``
        dialogue_object_classes (dict): Dictionary specifying the DialogueObject
            class for each dialogue type. See ``dialogue_objects`` definition above.
        opts (argparse.Namespace): Parsed command line arguments specifying parameters in agent.

            Args:
                --nsp_models_dir: Path to directory containing all files necessary to
                    load and run the model, including args, tree mappings and the checkpointed model.
                    Semantic parsing models used by current project are in ``ttad_bert_updated``.
                    eg. semantic parsing model is ``ttad_bert_updated/caip_test_model.pth``
                --nsp_data_dir: Path to directory containing all datasets used by the NSP model.
                    Note that this data is not used in inference, rather we load from the ground truth
                    data directory.
                --ground_truth_data_dir: Path to directory containing ground truth datasets
                    loaded by agent at runtime. Option to include a file for blacklisted words ``safety.txt``,
                    a class for greetings ``greetings.json`` and .txt files with text, logical_form pairs in
                    ``datasets/``.

            See :class:`ArgumentParser` for full list of command line options.

    """

    def __init__(self, agent, dialogue_object_classes, opts):
        super(NSPDialogueManager, self).__init__(agent, None)
        # Write file headers to the NSP outputs log
        self.dialogue_objects = dialogue_object_classes
        safety_words_path = opts.ground_truth_data_dir + "safety.txt"
        if os.path.isfile(safety_words_path):
            self.safety_words = self.get_safety_words(safety_words_path)
        else:
            self.safety_words = []
        # Load bot greetings
        greetings_path = opts.ground_truth_data_dir + "greetings.json"
        if os.path.isfile(greetings_path):
            with open(greetings_path) as fd:
                self.botGreetings = json.load(fd)
        else:
            self.botGreetings = {"hello": ["hi", "hello", "hey"], "goodbye": ["bye"]}

        # Instantiate the main model
        try:
            self.model = DialogModel(opts.nsp_models_dir, opts.nsp_data_dir)
        except NotADirectoryError:
            pass

        self.debug_mode = False

        self.ground_truth_actions = {}
        if not opts.no_ground_truth:
            if os.path.isdir(opts.ground_truth_data_dir):
                files = glob(opts.ground_truth_data_dir + "datasets/*.txt")
                for dataset in files:
                    with open(dataset) as f:
                        for line in f.readlines():
                            text, logical_form = line.strip().split("|")
                            clean_text = text.strip('"')
                            self.ground_truth_actions[clean_text] = ast.literal_eval(logical_form)

        self.dialogue_object_parameters = {
            "agent": self.agent,
            "memory": self.agent.memory,
            "dialogue_stack": self.dialogue_stack,
        }

        @sio.on("queryParser")
        def query_parser(sid, data):
            logging.debug("inside query parser.....")
            logging.debug(data)
            x = self.get_logical_form(s=data["chat"], model=self.model)
            logging.debug(x)
            payload = {"action_dict": x}
            sio.emit("renderActionDict", payload)


    def maybe_get_dialogue_obj(self, chat: Tuple[str, str]) -> Optional[DialogueObject]:
        """Process a chat and maybe modify the dialogue stack.

        Args:
            chat (Tuple[str, str]): Incoming chat from a player.
                Format is (speaker, chat), eg. ("player1", "build a red house")

        Returns:
            DialogueObject or empty if no action is needed.

        """

        if len(self.dialogue_stack) > 0 and self.dialogue_stack[-1].awaiting_response:
            return None

        # chat is a single line command
        speaker, chatstr = chat
        preprocessed_chatstrs = preprocess.preprocess_chat(chatstr)

        # Push appropriate DialogueObjects to stack if incoming chat
        # is one of the scripted ones
        for greeting_type in self.botGreetings:
            if any([chat in self.botGreetings[greeting_type] for chat in preprocessed_chatstrs]):
                return BotGreet(greeting_type, **self.dialogue_object_parameters)

        # NOTE: preprocessing in model code is different, this shouldn't break anything
        logical_form = self.get_logical_form(s=preprocessed_chatstrs[0], model=self.model)
        return self.handle_logical_form(speaker, logical_form, preprocessed_chatstrs[0])

    def handle_logical_form(self, speaker: str, d: Dict, chatstr: str) -> Optional[DialogueObject]:
        """Return the appropriate DialogueObject to handle an action dict d
        d should have spans filled (via process_spans_and_remove_fixed_value).
        """
        coref_resolve(self.agent.memory, d, chatstr)
        logging.info('logical form post-coref "{}" -> {}'.format(hash_user(speaker), d))
        ProgramNode.create(self.agent.memory, d)

        if d["dialogue_type"] == "NOOP":
            return Say("I don't know how to answer that.", **self.dialogue_object_parameters)
        elif d["dialogue_type"] == "GET_CAPABILITIES":
            return self.dialogue_objects["bot_capabilities"](**self.dialogue_object_parameters)
        elif d["dialogue_type"] == "HUMAN_GIVE_COMMAND":
            return self.dialogue_objects["interpreter"](
                speaker, d, **self.dialogue_object_parameters
            )
        elif d["dialogue_type"] == "PUT_MEMORY":
            return self.dialogue_objects["put_memory"](
                speaker, d, **self.dialogue_object_parameters
            )
        elif d["dialogue_type"] == "GET_MEMORY":
            logging.debug("this model out: %r" % (d))
            return self.dialogue_objects["get_memory"](
                speaker, d, **self.dialogue_object_parameters
            )
        else:
            raise ValueError("Bad dialogue_type={}".format(d["dialogue_type"]))

    def get_logical_form(self, s: str, model, chat_as_list=False) -> Dict:
        """Get logical form output for a given chat command.
        First check the ground truth file for the chat string. If not
        in ground truth, query semantic parsing model to get the output.

        Args:
            s (str): Input chat provided by the user.
            model (TTADBertModel): Semantic parsing model, pre-trained and loaded
                by agent

        Return:
            Dict: Logical form representation of the task. See paper for more
                in depth explanation of logical forms:
                https://arxiv.org/abs/1907.08584

        Examples:
            >>> get_logical_form("destroy this", model)
            {
                "dialogue_type": "HUMAN_GIVE_COMMAND",
                "action_sequence": [{
                    "action_type": "DESTROY",
                    "reference_object": {
                        "filters": {"contains_coreference": "yes"},
                        "text_span": [0, [1, 1]]
                    }
                }]
            }
        """
        return model.get_logical_form(s, chat_as_list, self.ground_truth_actions)


class NSPLogger():
    def __init__(self, filepath, headers):
        """Logger class for the NSP component.

        args:
            filepath (str): Where to log data.
            headers (list): List of string headers to be used in data store.
        """
        self.log_filepath = filepath
        self.init_file_headers(filepath, headers)

    def init_file_headers(self, filepath, headers):
        """Write headers to log file.

        args:
            filepath (str): Where to log data.
            headers (list): List of string headers to be used in data store.
        """
        with open(filepath, "w") as fd:
            csv_writer = csv.writer(fd, delimiter="|")
            csv_writer.writerow(headers)

    def log_dialogue_outputs(self, data):
        """Log dialogue data.

        args:
            filepath (str): Where to log data.
            data (list): List of values to write to file.
        """
        with open(self.log_filepath, "a") as fd:
            csv_writer = csv.writer(fd, delimiter="|")
            csv_writer.writerow(data)

class DialogModel:
    def __init__(self, models_dir, data_dir):
        """The DialogModel converts natural language commands to logical forms.
        
        Instantiates the ML model used for semantic parsing, ground truth data
        directory and sets up the NSP logger to save dialogue outputs.

        NSP logger schema:
        - command (str): chat command received by agent
        - action_dict (dict): logical form output
        - source (str): the source of the logical form, eg. model or ground truth
        - agent (str): the agent that processed the command
        - time (int): current time in UTC

        args:
            models_dir (str): path to semantic parsing models
            data_dir (str): path to ground truth data directory
        """
        # Instantiate the main model
        ttad_model_dir = os.path.join(models_dir, "ttad_bert_updated")
        logging.info("using model_dir={}".format(ttad_model_dir))

        if os.path.isdir(data_dir) and os.path.isdir(ttad_model_dir):
            from ttad.ttad_transformer_model.query_model import TTADBertModel as Model

            self.model = Model(model_dir=ttad_model_dir, data_dir=data_dir)
        else:
            raise NotADirectoryError
        self.NSPLogger = NSPLogger("nsp_outputs.csv", ["command", "action_dict", "source", "agent", "time"])

    def validate_parse_tree(self, parse_tree: dict) -> bool:
        """Validate the parse tree against current grammar.
        """
        # RefResolver initialization requires a base schema and URI
        schema_dir = "{}/".format(pkg_resources.resource_filename('base_agent.documents', 'json_schema'))
        json_validator = JSONValidator(schema_dir, span_type="all")
        is_valid_json = json_validator.validate_instance(parse_tree)
        return is_valid_json

    def get_logical_form(self, s: str, chat_as_list=False, ground_truth_actions: dict={}) -> Dict:
        """Get logical form output for a given chat command.
        First check the ground truth file for the chat string. If not
        in ground truth, query semantic parsing model to get the output.

        Args:
            s (str): Input chat provided by the user.
            chat_as_list (bool): if True, expects `s` to be a list of strings
            ground_truth_actions (dict): A list of ground truth actions to pre-match against

        Return:
            Dict: Logical form representation of the task. See paper for more
                in depth explanation of logical forms:
                https://arxiv.org/abs/1907.08584

        Examples:
            >>> get_logical_form("destroy this", model)
            {
                "dialogue_type": "HUMAN_GIVE_COMMAND",
                "action_sequence": [{
                    "action_type": "DESTROY",
                    "reference_object": {
                        "filters": {"contains_coreference": "yes"},
                        "text_span": [0, [1, 1]]
                    }
                }]
            }
        """
        if s in ground_truth_actions:
            d = ground_truth_actions[s]
            logging.info('Found ground truth action for "{}"'.format(s))
            # log the current UTC time
            time_now = time()
            self.NSPLogger.log_dialogue_outputs([s, d, "ground_truth", "craftassist", time_now])
        else:
            logging.info("Querying the semantic parsing model")
            if chat_as_list:
                d = self.model.parse([s])
            else:
                d = self.model.parse(chat=s)
            # log the current UTC time
            time_now = time()
            self.NSPLogger.log_dialogue_outputs([s, d, "semantic_parser", "craftassist", time_now])

        # Validate parse tree against grammar
        is_valid_json = self.validate_parse_tree(d)
        if not is_valid_json:
            # Send a NOOP
            logging.error("Invalid parse tree for command {}\n".format(s))
            logging.error("Parse tree failed grammar validation: \n{}\n".format(d))
            d = {"dialogue_type": "NOOP"}
            logging.error("Returning NOOP")
            return d

        # perform lemmatization on the chat
        logging.debug('chat before lemmatization "{}"'.format(s))
        lemmatized_chat = spacy_model(s)
        chat = " ".join(str(word.lemma_) for word in lemmatized_chat)
        logging.debug('chat after lemmatization "{}"'.format(chat))

        # Get the words from indices in spans and substitute fixed_values
        process_spans_and_remove_fixed_value(d, re.split(r" +", s), re.split(r" +", chat))
        logging.debug("process")
        logging.debug('ttad pre-coref "{}" -> {}'.format(chat, d))

        # log to sentry
        sentry_sdk.capture_message(
            json.dumps({"type": "ttad_pre_coref", "in_original": s, "out": d})
        )
        sentry_sdk.capture_message(
            json.dumps({"type": "ttad_pre_coref", "in_lemmatized": chat, "out": d})
        )

        logging.debug('logical form before grammar update "{}'.format(d))
        logging.debug('logical form after grammar fix "{}"'.format(d))

        return d
