import os
import sys
import copy
import pprint as pp
import argparse
import json
import pkg_resources

"""Applies updates to annotated dataset following grammar changes.
"""

def read_file(file_path):
    with open(file_path) as fd:
        dataset = fd.readlines()
    return dataset

def update_tree():
    raise NotImplementedException

def traverse_tree(command, action_dict):
    traverse_subtree(command, action_dict)
    print("final tree:")
    pp.pprint(action_dict)
    print(action_dict)
    return action_dict

def get_span_range(text, command):
    index = command.find(text)
    if index == -1:
        return index
    else:
        words_arr = command.split(" ")
        text_arr = text.split(" ")
        for i in range(len(words_arr)):
            if words_arr[i] == text_arr[0]:
                words_arr_set = " ".join(words_arr[i:i+len(text_arr)])
                if words_arr_set == text:
                    return [0, [i, i+len(text_arr) -1]]

        return -1

def traverse_subtree(command, action_dict):
    for key, value in [x for x in action_dict.items()]:
        if type(value) == dict:
            traverse_subtree(command, value)
        if type(value) == list:
            if type(value[0]) == dict:
                for ad in value:
                    traverse_subtree(command, ad)
        if value == "":
            del action_dict[key]
    return action_dict

def write_file(dataset, file_path):
    with open(file_path, "w") as fd:
        for line in dataset:
            fd.write(line + "\n")

if __name__ == "__main__":
    print("*** Applying grammar updates ***")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source_path", default="command_dict_pairs.json")
    args = parser.parse_args()
    # load the annotated dataset
    with open(args.source_path) as fd:
        dataset = json.load(fd)
    autocomplete_annotations = {}
    updated_dataset = []
    datasets_read_path = "{}/{}".format(pkg_resources.resource_filename('craftassist.agent', 'datasets'), "full_data/high_pri_commands.txt")

    # Read the file, update  
    for command in dataset:
        action_dict = dataset[command]
        updated_tree = traverse_tree(command, action_dict)
        autocomplete_annotations[command] = updated_tree
    # Load all the existing action dictionaries
    with open(datasets_read_path) as fd:
        existing_annotations = fd.readlines()
        for row in existing_annotations:
            command, action_dict = row.strip().split("|")
            if command not in autocomplete_annotations:
                autocomplete_annotations[command] = json.loads(action_dict)

    for command in autocomplete_annotations:
        updated_dataset.append("{}|{}".format(command, json.dumps(autocomplete_annotations[command])))
    write_file(updated_dataset, datasets_read_path)
