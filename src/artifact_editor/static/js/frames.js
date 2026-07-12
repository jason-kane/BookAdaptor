function beforeFrameRequest(event) {
    var obj = document.querySelector('#frame_image');
    obj.setAttribute('loading', '');

    var button = document.querySelector('#redraw_button');
    if (button) {
        button.setAttribute('disabled', '');
        button.setAttribute('loading', '');
    }
}

function afterFrameRequest(event) {
    var obj = document.querySelector('#frame_image');
    obj.removeAttribute('loading');
    htmx.process(obj);
    
    var button = document.querySelector('#redraw_button');
    if (button) {
        button.removeAttribute('disabled');
        button.removeAttribute('loading');
    }    
}
