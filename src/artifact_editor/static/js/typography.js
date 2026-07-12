
var { pdfjsLib } = globalThis;

// we can't use canvas until the DOM is loaded.
var pdfDoc = null,
    pageNum = 1,
    pageRendering = false,
    pageNumPending = null,
    scale = 2.0;

function renderPage(num) {
    var canvas = document.getElementById('the-canvas'),
        ctx = canvas.getContext('2d');

    pageRendering = true;
    // Using promise to fetch the page
    pdfDoc.getPage(num).then(function (page) {
        var viewport = page.getViewport({ scale: scale });
        var outputScale = window.devicePixelRatio || 1;

        canvas.width = viewport.width
        //Math.floor(viewport.width * outputScale);
        canvas.height = viewport.height
        //Math.floor(viewport.height * outputScale);
        //canvas.style.width = Math.floor(viewport.width) + "px";
        //canvas.style.height =  Math.floor(viewport.height) + "px";

        var transform = outputScale !== 1
            ? [outputScale, 0, 0, outputScale, 0, 0]
            : null;

        // Render PDF page into canvas context
        var renderContext = {
            canvasContext: ctx,
            transform: transform,
            viewport: viewport
        };
        var renderTask = page.render(renderContext);

        // Wait for rendering to finish
        renderTask.promise.then(function () {
            pageRendering = false;
            if (pageNumPending !== null) {
                // New page rendering is pending
                renderPage(pageNumPending);
                pageNumPending = null;
            }
        });
    });

    // Update page counters
    document.getElementById('page_num').textContent = num;
}

/**
 * If another page rendering in progress, waits until the rendering is
 * finised. Otherwise, executes rendering immediately.
 */
function queueRenderPage(num) {
    if (pageRendering) {
        pageNumPending = num;
    } else {
        renderPage(num);
    }
}

/**
 * Displays previous page.
 */
function onPrevPage() {
    if (pageNum <= 1) {
        return;
    }
    pageNum--;
    queueRenderPage(pageNum);
}

function attachEventListeners() {
    document.getElementById('prev').addEventListener('click', onPrevPage);
    document.getElementById('next').addEventListener('click', onNextPage);
}

document.addEventListener('DOMContentLoaded', attachEventListeners);

/**
 * Displays next page.
 */
function onNextPage() {
    if (pageNum >= pdfDoc.numPages) {
        return;
    }
    pageNum++;
    queueRenderPage(pageNum);
}

function latex_rerender(button, event) {
    // we just drew a new .pdf from the latex file
    /**
     * Asynchronously downloads PDF.
     */
    var url = 'chapter.pdf' + '?t=' + new Date().getTime(); // add timestamp to prevent caching
    pdfjsLib.getDocument(url).promise.then(function (pdfDoc_) {
        pdfDoc = pdfDoc_;
        document.getElementById('page_count').textContent = pdfDoc.numPages;

        // Initial/first page rendering
        if (document.readyState === "loading") {
          // DOM is still loading; wait for it
          document.addEventListener("DOMContentLoaded", function() {
              renderPage(pageNum);
          });
        } else {
          // DOM is already "interactive" or "complete"; call now
          renderPage(pageNum);
        }        
    });
};
