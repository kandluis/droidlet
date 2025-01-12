import React from 'react';
import ReactDOM from 'react-dom';
import autocompleteMatches from './spec/grammar_spec';


class FiltersAnnotator extends React.Component {

  constructor(props) {
    super(props);
    this.state = {
      value: '',
      currIndex: -1,
      fullText: [],
      dataset: {},
    }
    /* Array of text commands that need labelling */
    this.handleChange = this.handleChange.bind(this);
    this.logSerialized = this.logSerialized.bind(this);
    this.uploadData = this.uploadData.bind(this);
    this.incrementIndex = this.incrementIndex.bind(this);
    this.decrementIndex = this.decrementIndex.bind(this);
    this.componentDidMount = this.componentDidMount.bind(this);
    this.callAPI = this.callAPI.bind(this);
    this.goToIndex = this.goToIndex.bind(this);
    this.updateLabels = this.updateLabels.bind(this);
  }

  callAPI(data) {
    const requestOptions = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    };
    fetch("http://localhost:9000/readAndSaveToFile/append", requestOptions)
      .then(
        (result) => {
          console.log(result)
          this.setState({ value: "" })
          alert("saved!")
        },
        (error) => {
          console.log(error)
        }
      )
  }

  writeLabels(data) {
    const requestOptions = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    };
    fetch("http://localhost:9000/readAndSaveToFile/writeLabels", requestOptions)
      .then(
        (result) => {
          console.log("success")
          console.log(result)
          this.setState({ value: "" })
          alert("saved!")
        },
        (error) => {
          console.log(error)
        }
      )
  }

  componentDidMount() {
    fetch("http://localhost:9000/readAndSaveToFile/get_commands")
      .then(res => res.text())
      .then((text) => { this.setState({ fullText: text.split("\n").filter(r => r !== "") }) })

    fetch("http://localhost:9000/readAndSaveToFile/get_labels_progress")
      .then(res => res.json())
      .then((data) => { this.setState({ dataset: data})})
      .then(() => console.log(this.state.dataset))
  }

  handleChange(e) {
    this.setState({ value: e.target.value });
  }

  incrementIndex() {
    console.log("Moving to the next command")
    console.log(this.state.currIndex)
    console.log(this.state.fullText)
    if (this.state.currIndex + 1 >= this.state.fullText.length) {
      alert("Congrats! You have reached the end of annotations.")
    }
    this.setState({ currIndex: this.state.currIndex + 1, value: JSON.stringify(this.state.dataset[this.state.fullText[this.state.currIndex + 1]] ?? "")});
  }

  decrementIndex() {
    console.log("Moving to the next command")
    console.log(this.state.currIndex)
    this.setState({ currIndex: this.state.currIndex - 1, value: JSON.stringify(this.state.dataset[this.state.fullText[this.state.currIndex - 1]] ?? "")});
  }

  goToIndex(i) {
    console.log("Fetching index " + i)
    this.setState({ currIndex: Number(i), value: JSON.stringify(this.state.dataset[this.state.fullText[i]] ?? "")});
    console.log(this.state.dataset)
  }

  updateLabels(e) {
    // Make a shallow copy of the items
    try {
      // First check that the string is JSON valid
      let JSONActionDict = JSON.parse(this.state.value)
      let items = {...this.state.dataset};
      items[this.state.fullText[this.state.currIndex]] = JSONActionDict;
      // Set state to the data items
      this.setState({dataset: items}, function() {
        try {
          let actionDict = JSONActionDict
          let JSONString = {
            "command": this.state.fullText[this.state.currIndex],
            "logical_form": actionDict
          }
          console.log("writing dataset")
          console.log(this.state.dataset)
          this.writeLabels(this.state.dataset)
        } catch (error) {
          console.error(error)
          console.log("Error parsing JSON")
          alert("Error: Could not save logical form. Check that JSON is formatted correctly.")
        }
      });
    } catch(error) {
      console.error(error)
      console.log("Error parsing JSON")
      alert("Error: Could not save logical form. Check that JSON is formatted correctly.")
    }
  }

  logSerialized() {
    console.log("saving serialized tree")
    // First save to local storage
    this.updateLabels()
  }

  uploadData() {
    console.log("Uploading Data to S3")
    // First postprocess
    const requestOptions = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    };
    fetch("http://localhost:9000/readAndSaveToFile/uploadDataToS3", requestOptions)
    .then(
      (result) => {
        if (result.status == 200) {
          this.setState({ value: "" })
          alert("saved!")
        } else {
          alert("Error: could not upload data to S3: " + result.statusText + "\n Check the format of your action dictionary labels.")
        }
      },
      (error) => {
        console.error(error)
      }
    )
  }


  render() {
    return (
      <div>
        <b> Command </b>
        <TextCommand fullText={this.state.fullText} currIndex={this.state.currIndex} incrementIndex={this.incrementIndex} decrementIndex={this.decrementIndex} prevCommand={this.incrementIndex} goToIndex={this.goToIndex} />
        <LogicalForm currIndex={this.state.currIndex} value={this.state.value} onChange={this.handleChange} />
        <div onClick={this.logSerialized}>
          <button>Save</button>
        </div>
        <div onClick={this.uploadData}>
          <button>Upload to S3</button>
        </div>
      </div>
    )
  }
}

// Represents a Text Input node
class LogicalForm extends React.Component {
  constructor(props) {
    super(props)
    this.keyPress = this.keyPress.bind(this)
  }

  keyPress(e) {
    // Hit enter
    if (e.keyCode == 13) {
      let autocompletedResult = e.target.value
      // Apply replacements
      autocompleteMatches.forEach(replacer => {
        autocompletedResult = autocompletedResult.replace(replacer.match, replacer.replacement)
      })

      console.log(autocompletedResult)
      var obj = JSON.parse(autocompletedResult);
      var pretty = JSON.stringify(obj, undefined, 4);
      console.log(pretty)

      e.target.value = pretty
    }
  }

  render() {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', marginBottom: 10, marginTop: 10 }} >
        <b> Action Dictionary </b>
        <textarea rows="20" cols="100" value={this.props.value} onKeyDown={this.keyPress} onChange={(e) => this.props.onChange(e)} fullWidth={false} />
      </div>
    )
  }
}

// Represents a Text Input node
class TextCommand extends React.Component {
  constructor(props) {
    super(props)
    this.fullText = props.fullText
    this.state = {
      value: "",
      currIndex: 0,
      indexValue: 0,
    }
    this.incrementIndex = props.incrementIndex
    this.handleChange = this.handleChange.bind(this)
  }

  handleChange(e) {
    this.setState({ indexValue: e.target.value });
  }

  render() {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', marginBottom: 5, marginTop: 5 }}>
        <div onClick={this.props.decrementIndex}>
          <button>Prev</button>
        </div>
        <div onClick={this.props.incrementIndex}>
          <button>Next</button>
        </div>
        <div>
          <span>Index: <input onChange={this.handleChange} value={this.props.currIndex} type="number"></input></span>
          <button onClick={(param) => this.props.goToIndex(this.state.indexValue)}> Go! </button>
        </div>
        <textarea rows="2" cols="10" value={this.props.fullText[this.props.currIndex]} fullWidth={false} />
      </div>
    )
  }
}


export default FiltersAnnotator;