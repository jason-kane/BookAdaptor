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
// Capture the field directly so generation includes edits still waiting to save.
document.addEventListener('htmx:wsConfigSend', event => {
    if (event.detail.parameters.action !== 'character_image') return;
    const tag = event.detail.parameters.character_tag;
    const description = document.getElementById(`${tag}_description`)?.value || '';
    if (!description.trim()) {
        event.preventDefault();
        document.getElementById(`${tag}_description`)?.focus();
        return;
    }
    event.detail.parameters.description = description;
});
document.addEventListener('htmx:wsAfterSend', event => {
    const container = event.detail.elt?.closest('[data-character-image]');
    if (container) container.querySelector('wa-button').disabled = true;
});
document.addEventListener('htmx:wsAfterMessage', () => {
    document.querySelectorAll('[data-character-image]').forEach(container => {
        const status = document.getElementById(`video-progress-${container.dataset.characterImage}`);
        const state = status?.dataset.videoState;
        if (state !== 'complete' && state !== 'failed') return;
        const button = container.querySelector('wa-button');
        button.disabled = false;
        if (state === 'complete' && !status.dataset.imageRefreshed) {
            const image = document.createElement('img');
            image.src = `${container.dataset.imageSrc}?v=${Date.now()}`;
            image.alt = 'Full-body character image';
            image.style.cssText = 'max-width:100%;max-height:600px;object-fit:contain';
            container.querySelector('[data-character-preview]').replaceChildren(image);
            button.textContent = 'Regenerate Image';
            status.dataset.imageRefreshed = 'true';
        }
    });
});
