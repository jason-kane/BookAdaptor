

window.onload = function() {
    document.body.addEventListener("newCarouselReady", addCarouselListeners, false);

    
};

function removeCarouselListeners() {
    const scroller = document.querySelector('.scroller');
    scroller.removeEventListener('click', listener);
}

function removeCarouselImage(button, event) {
    const thumbnails = document.querySelectorAll('.image');
    const bigPicture = document.querySelector('.carousel-big-picture');

    // Find the active thumbnail
    let found = false;

    [...thumbnails].forEach((thumb, i) => {
        if (!(found) && (thumb.classList.contains('active'))) {
            found = true;
            // remove this thumbnail from the DOM
            thumb.remove();

            // if this isn't the last thumbnail, select the next thumbnail.
            if (i < thumbnails.length - 1) {
                const nextThumb = thumbnails[i + 1];
                nextThumb.classList.add('active');
                bigPicture.src = nextThumb.src;
            } else if (i > 0) {
                // if this is the last thumbnail, select the previous thumbnail.
                const prevThumb = thumbnails[i - 1];
                prevThumb.classList.add('active');
                bigPicture.src = prevThumb.src;
            } else {
                // this was the last image, its cool.
                bigPicture.src = "";
                document.querySelector("#image_metadata").innerHTML = "";
            }
        }
    });

    afterRequest(button, event);
}

function listener(event) {
    const target = event.target;
    const bigPicture = document.querySelector('.carousel-big-picture');
    const thumbnails = document.querySelectorAll('.image');

    if (target.matches('.image')) {
        const index = [...thumbnails].indexOf(target);
        // bigPicture is an img element, we want to change the src to mirror
        // 'target', another img element.
        bigPicture.src = target.src;

        // highlight the active thumbnail, which happens to be 'target'
        [...thumbnails].forEach((thumb, i) => {
            thumb.classList.toggle('active', i === index);
        });

        // update the .image_metadata div with the metadata
        htmx.ajax(
            'GET',
            "selector/get_image_metadata?src=" + target.src.split("/").pop(),
            {
                target: "#image_metadata",
                swap: "outerHTML transition:true"
            }
        ).then(() => {}).catch(() => {  
            // if the metadata request fails, clear the metadata div
            document.querySelector("#image_metadata").innerHTML = "";
        });
    }
}

function addCarouselListeners() {
    //const carousel = document.querySelector('.carousel-thumbnails');
    const scroller = document.querySelector('.scroller');
    //const thumbnails = document.querySelectorAll('.image');

    // add event listeners
    // clicking on the thumbnail image
    if (scroller && scroller.addEventListener) {
        scroller.addEventListener('click', listener);
    }
}

