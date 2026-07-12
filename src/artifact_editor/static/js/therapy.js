// let mediaRecorder;
// let audioChunks = [];
// let audioPlayback = document.getElementById('audio-playback');
// let startButton = document.getElementById('start-button');
// let stopButton = document.getElementById('stop-button');
// let downloadLink = document.getElementById('download-link');

// startButton.addEventListener('click', startRecording);
// stopButton.addEventListener('click', stopRecording);

async function startRecording() {
    try {
        // Request access to the microphone
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        mediaRecorder = new MediaRecorder(stream);
        
        mediaRecorder.ondataavailable = event => {
            audioChunks.push(event.data);
        };

        mediaRecorder.onstop = () => {
            // Combine all recorded chunks into a single Blob
            const blob = new Blob(audioChunks, { type: 'audio/webm' }); 
            const audioUrl = URL.createObjectURL(blob);
            
            // Set the audio source for playback
            audioPlayback.src = audioUrl;
            
            // Enable download link
            downloadLink.href = audioUrl;
            downloadLink.download = 'microphone-recording.webm';
            downloadLink.style.display = 'block';

            // Stop all tracks in the stream to turn off the microphone light
            stream.getTracks().forEach(track => track.stop());
            audioChunks = [];
        };

        mediaRecorder.start();
        startButton.disabled = true;
        stopButton.disabled = false;
        console.log("Recording started...");
    } catch (err) {
        console.error('Error accessing microphone:', err);
        alert('Could not access the microphone. Please allow access.');
    }
}

function stopRecording() {
    mediaRecorder.stop();
    startButton.disabled = false;
    stopButton.disabled = true;
    console.log("Recording stopped.");
}

function startListening() {
    console.log("Listening for voice commands...");
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.start();

    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        console.log('Voice command received:', transcript);
        document.getElementById('say-textarea').value = transcript;
        recognition.stop();
    };
}

function beforeConversationRequest(obj, event) {
    console.log("Conversation request started.");
    const textarea = document.getElementById('say-textarea');

    // busy spinner on the textarea
    textarea.classList.add('loading');
}

function afterConversationRequest(obj, event) {
    console.log("Conversation request completed.");

    // Clear the textarea after sending the message
    const textarea = document.getElementById('say-textarea');
    textarea.value = '';
    textarea.classList.remove('loading');
}