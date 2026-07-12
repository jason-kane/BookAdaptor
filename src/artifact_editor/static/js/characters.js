function character_has_voice(character_name) {
    // this character has a voice, it may or may not have had one before.
    // this may change the enabled/disabled state of the 'Randomize Voice Weights' 
    // button
    let button = document.getElementById(character_name + "_randomize_voices");
    // remove the 'disabled' attribute if it exists
    button.removeAttribute("disabled");
}

function voice_weights_have_changed(character_name) {
    // are any of this character's voices strength > 0?
    
    let has_positive_voice_weights = false;
    let mixers = document.querySelectorAll("#" + character_name + "_voice_selection_panel .wa-slider");
    for (let i = 0; i < mixers.length; i++) {
        let mixer = mixers[i];
        if (mixer.value > 0) {
            has_positive_voice_weights = true;
            break;
        }
    }

    if (has_positive_voice_weights) {
        let remove_button = document.getElementById(character_name + "_remove_voices");
        remove_button.removeAttribute("disabled");
    } else {
        let remove_button = document.getElementById(character_name + "_remove_voices");
        remove_button.setAttribute("disabled", "true");
    }
}