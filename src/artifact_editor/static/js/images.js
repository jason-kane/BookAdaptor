
// https://github.com/bigskysoftware/htmx/issues/3622#issuecomment-3781315963
const elementsToFix = ["wa-select", "wa-input", "wa-radio", "wa-radio-group", "wa-button", "wa-checkbox"];

for (const element of elementsToFix) {
    customElements.whenDefined(element).then(ctor => {
        const original = ctor.prototype.focus;
        ctor.prototype.focus = function(options) {
            if (this.hasUpdated) {
                original.call(this, options);
            } else {
                this.updateComplete.then(() => original.call(this, options));
            }
        };
    });
}

function get_selected_video(event, video_index) {
    return {
        selected_video,
        video_index
    };
}   

function getSelectedImageSrc() {
    return document.querySelector('.carousel-big-picture').src;
}

let cropper;

function chooseRegion() {
    cropper = new Cropper.default(".carousel-big-picture", {
        viewMode: 1
    });

    const cropperImage = cropper.getCropperImage();
    cropperImage.$setTransform(cropperImage, [1, 0, 0, 1, 0, 0]);

    const cropperSelection = cropper.getCropperSelection();
    cropperSelection.$toCanvas({
        width: 400,
        height: 400
    });

    console.log("Button clicked!");
}

function getRegionDimensions() {
    console.log("Getting region dimensions...");
    if (!cropper) {
        const toast = document.getElementById('toast');
        toast.create("Choose region first!");
        console.error("Cropper not initialized. Please click the 'Choose Region' button first.");
        return null;
    }
    const selection = cropper.getCropperSelection();
    //relative to the canvas
    const { x, y, width, height } = selection;
    
    const imageElement = cropper.getCropperImage();
    const transform = imageElement.$getTransform(); 
    
    console.log('transform:' + transform);
    console.log('height:' + height);
    console.log('width:' + width);
    const out = {
        x: (1080 / 400) * x / transform[0],
        y: (1080 / 400) * y / transform[3],
        width: (1080 / 400) * width / transform[0],
        height: (1080 / 400) * height / transform[3]
    };
    console.log(out);
    return out;

}


// //Wait for the DOM to be fully loaded before attaching the event listener
var selected_video = null;
document.addEventListener('DOMContentLoaded', () => {
    const playlist = document.getElementById('video_playlist');
    if (playlist) {
        playlist.addEventListener('wa-video-change', event => {
            selected_video = event.detail.video;
        });
    }
});
