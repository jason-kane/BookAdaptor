
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

    // return document.querySelector(
    //     "#image-selector > wa-carousel-item.--in-view"
    // ).children[0].getAttribute("src");
}

let cropper;

function chooseRegion() {
    //const image = document.querySelector(".carousel-big-picture");
    //image.onload = () => {
        cropper = new Cropper.default(".carousel-big-picture", {
            viewMode: 1
        });
        // image, {
        //     viewMode: 1,
        //     autoCrop: false,
        //     responsive: true,
        //     background: false,
        //     zoomable: false,
        //     movable: false,
        //     rotatable: false,
        //     scalable: false,
        // });
        // const matrix = cropperImage.$getTransform();

        // // Try to calculate the following values with the `matrix` by yourself.
        // const visualX = ?;
        // const visualY = ?;
        // const visualWidth = ?;
        // const visualHeight = ?;
        const cropperImage = cropper.getCropperImage();
        cropperImage.$setTransform(cropperImage, [1, 0, 0, 1, 0, 0]);

        const cropperSelection = cropper.getCropperSelection();
        cropperSelection.$toCanvas({
            width: 400,
            height: 400
        });
    //};
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
    document.getElementById('video_playlist').addEventListener('wa-video-change', event => {
        selected_video = event.detail.video;
    });
});

//     const btn = document.getElementById('choose_region_button');
//     if (btn) {
//         btn.addEventListener('click', chooseRegion);
//     }
// });

function chooseImage(chapter, language, index, all_but) {
    // const btn = document.getElementById('choose_region_button');
    // if (btn) {
    //     btn.addEventListener('click', chooseRegion);
    // }
    
    htmx.ajax(
        'GET',
        url_for("images.surrounding_text", author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=language, image_index=index,),
        "#surrounding_text"
    ).then(() => {
        htmx.ajax(
            'GET',
            "/" + chapterurl + "/images/forex/" + index,
            {
                target: "#forex",
                swap: "outerHTML transition:true"
            }
        ).then(() => {
            //const tg=document.querySelector("#image-tab-group")
            //tg.setAttribute('hx-post', '/' + chapter.url + '/' + chapter.language + '/images/' + index + '/#image_tab_selector')
            htmx.process(tg);

            // if (
            //     tg.children.hasOwnProperty('viewer')
            // ) {
            //     //tg.active = "";
            //     tg.active = "viewer";
            // } else {
            //     //tg.active = "";
            //     tg.active = "prompt";
            // }                

            // htmx.process(tg);
            
            // as-if the user re-clicked the currently selected tab
            const event = new Event('wa-tab-show', {
                name: tg.activeTab.panel,
                bubbles: true,
                cancelable: false,
                composed: true
            });
            tg.dispatchEvent(event);

            // if this page has a choose_region_button, enable it.

           
            //     htmx.ajax(
            //         'GET',
            //         "/" + chapterurl + "/images/metadata/" + index,
            //         {
            //             target: "#metadata",
            //             swap: "outerHTML transition:true"
            //         }
            //     );
            // });
        });
    });
}