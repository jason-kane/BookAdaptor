
// import { EditorView, basicSetup } from 'codemirror';
// import { CodeMirror } from 'codemirror';
// import { EditorView, basicSetup } from 'codemirror';
// import { EditorView } from "@codemirror/view";
// import { EditorState } from "@codemirror/state";
// import { basicSetup } from "@uiw/codemirror-extensions-basic-setup";
//import { latex } from 'codemirror-lang-latex';

import { EditorView, basicSetup } from "codemirror";
import { latex } from 'codemirror-lang-latex';
import {Compartment} from "@codemirror/state"
import {oneDark} from "@codemirror/theme-one-dark"

// https://github.com/bigskysoftware/htmx/issues/3622#issuecomment-3781315963
const elementsToFix = ["wa-select", "wa-input", "wa-radio", "wa-radio-group", "wa-button", "wa-checkbox"];

for (const element of elementsToFix) {
    customElements.whenDefined(element).then(ctor => {
        const original = ctor.prototype.focus;
        ctor.prototype.focus = function (options) {
            if (this.hasUpdated) {
                original.call(this, options);
            } else {
                this.updateComplete.then(() => original.call(this, options));
            }
        };
    });
}

function beforeMicrophoneRequest(obj, event) {
    // we are now listening, make that abundantly clear
    obj.classList.remove("idle");
    obj.classList.add("active");

    const icon = obj.querySelector("wa-icon");
    if (icon) {
        icon.classList.remove("idle");

        icon.classList.add("active");
        icon.setAttribute("src", "/static/fontawesome7/svgs/solid/microphone.svg");
    }
}

function afterMicrophoneRequest(obj, event) {
    obj.classList.remove("active");
    obj.classList.add("idle");

    const icon = obj.querySelector("wa-icon");
    if (icon) {
        icon.classList.remove("active");
        icon.classList.add("idle");
        icon.setAttribute("src", "/static/fontawesome7/svgs/solid/microphone-slash.svg");
    }
    htmx.process(obj);
}


function latex_editor_init() {
    console.log('Invoked latex_editor_init()')
    // for each wa-textarea with class phrase-latex, turn them into a div with a
    // unique id, and create a CodeMirror instance attached to each object.
    const textareas = document.querySelectorAll("wa-textarea.phrase-latex");
    const themeCompartment = new Compartment()

    textareas.forEach((textarea, index) => {
        console.log("Initializing CodeMirror for", textarea);
        const id = `latex-editor-${index}`;
        const div = document.createElement("div");
        div.id = id;
        div.classList.add("phrase-typography");
        div.classList.add('phrase-latex');
        textarea.replaceWith(div);

        // const fullHeightTheme = EditorView.theme({
        // // The "&" targets the editor's outer container (.cm-editor)
        // "&": { height: "100%" },
        // // The scroller element must be told to allow scrolling
        // ".cm-scroller": { overflow: "auto" }
        // })

        const editor = new EditorView({
            doc: textarea.getAttribute('value'),
            extensions: [
                basicSetup,
                latex(),
                themeCompartment.of(oneDark)
            ],  //, fullHeightTheme],
            lineNumbers: true,
            theme: 'monokai',
            parent: div,
            viewportMargin: Infinity
        });
    });
}

document.addEventListener("DOMContentLoaded", latex_editor_init);
document.addEventListener("latex_editor_init", latex_editor_init);


async function localSoundSelector() {
    const fileHandle = await self.showOpenFilePicker({
        startIn: "music",
    });
}