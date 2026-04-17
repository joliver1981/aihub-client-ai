/**
 * Speech Recognition Module with Whisper API Support
 * Drop-in replacement - works with existing implementations
 * Can use Web Speech API OR Whisper API (configurable)
 */

class SpeechRecognitionManager {
    constructor(config = {}) {
        this.config = {
            language: config.language || 'en-US',
            continuous: config.continuous || false,
            interimResults: config.interimResults !== undefined ? config.interimResults : true,
            maxAlternatives: config.maxAlternatives || 1,
            inputElementId: config.inputElementId || 'message-input',
            buttonElementId: config.buttonElementId || 'mic-button',
            onStart: config.onStart || null,
            onEnd: config.onEnd || null,
            onResult: config.onResult || null,
            onError: config.onError || null,
            
            // Whisper API Configuration
            // Set forceWhisper: true to always use Whisper (ignores browser support)
            // Set useWhisperFallback: true to use Whisper only if Web Speech not available
            forceWhisper: config.forceWhisper || false,
            useWhisperFallback: config.useWhisperFallback !== undefined ? config.useWhisperFallback : false,
            whisperEndpoint: config.whisperEndpoint || '/api/transcribe',
            whisperMaxDuration: config.whisperMaxDuration || 30000, // 30 seconds max
        };

        this.recognition = null;
        this.isListening = false;
        this.isSupported = this.checkSupport();
        this.finalTranscript = '';
        this.interimTranscript = '';
        
        // Recording for Whisper API
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.recordingTimeout = null;
        
        // Determine which mode to use
        if (this.config.forceWhisper) {
            this.mode = 'whisper';
            console.log('Forced to use Whisper API');
        } else if (this.isSupported) {
            this.mode = 'webspeech';
            console.log('Using Web Speech API');
        } else if (this.config.useWhisperFallback) {
            this.mode = 'whisper';
            console.log('Web Speech API not supported, using Whisper API fallback');
        } else {
            this.mode = 'none';
        }
        
        if (this.mode === 'webspeech') {
            this.initWebSpeechAPI();
        }
    }

    /**
     * Check if Web Speech API is supported
     */
    checkSupport() {
        return 'SpeechRecognition' in window || 'webkitSpeechRecognition' in window;
    }

    /**
     * Initialize Web Speech API
     */
    initWebSpeechAPI() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        this.recognition = new SpeechRecognition();
        
        this.recognition.continuous = this.config.continuous;
        this.recognition.interimResults = this.config.interimResults;
        this.recognition.lang = this.config.language;
        this.recognition.maxAlternatives = this.config.maxAlternatives;

        this.setupWebSpeechHandlers();
    }

    /**
     * Set up event handlers for Web Speech API
     */
    setupWebSpeechHandlers() {
        this.recognition.onstart = () => {
            this.isListening = true;
            this.finalTranscript = '';
            this.interimTranscript = '';
            this.updateUI('listening');
            
            if (this.config.onStart) {
                this.config.onStart();
            }
        };

        this.recognition.onresult = (event) => {
            this.interimTranscript = '';
            
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                
                if (event.results[i].isFinal) {
                    this.finalTranscript += transcript + ' ';
                } else {
                    this.interimTranscript += transcript;
                }
            }

            const fullTranscript = this.finalTranscript + this.interimTranscript;
            this.updateInputElement(fullTranscript);

            if (this.config.onResult) {
                this.config.onResult({
                    final: this.finalTranscript,
                    interim: this.interimTranscript,
                    full: fullTranscript
                });
            }
        };

        this.recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            this.isListening = false;
            this.updateUI('error');
            
            const errorMessage = this.getErrorMessage(event.error);
            
            if (this.config.onError) {
                this.config.onError(event.error, errorMessage);
            } else {
                this.showNotification(errorMessage, 'error');
            }
        };

        this.recognition.onend = () => {
            this.isListening = false;
            this.updateUI('idle');
            
            if (this.config.onEnd) {
                this.config.onEnd(this.finalTranscript);
            }
        };
    }

    /**
     * Start listening (chooses appropriate mode)
     */
    start() {
        if (this.isListening) {
            this.stop();
            return false;
        }

        if (this.mode === 'webspeech') {
            return this.startWebSpeech();
        } else if (this.mode === 'whisper') {
            return this.startWhisperRecording();
        } else {
            this.showNotification('Speech recognition is not available in your browser', 'warning');
            return false;
        }
    }

    /**
     * Start Web Speech API
     */
    startWebSpeech() {
        try {
            this.recognition.start();
            return true;
        } catch (error) {
            console.error('Error starting recognition:', error);
            this.showNotification('Failed to start speech recognition', 'error');
            return false;
        }
    }

    /**
     * Check if microphone API is supported (synchronous)
     * Use this to hide/show button on page load
     */
    isMicrophoneSupported() {
        return !!(navigator.mediaDevices && 
                typeof navigator.mediaDevices.getUserMedia === 'function');
    }

    /**
     * Start recording for Whisper API
     */
    async startWhisperRecording() {
        try {
            // Request microphone access
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            this.isListening = true;
            this.audioChunks = [];
            this.updateUI('listening');
            
            if (this.config.onStart) {
                this.config.onStart();
            }

            // Create media recorder
            this.mediaRecorder = new MediaRecorder(stream);
            
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };

            this.mediaRecorder.onstop = async () => {
                // Stop all tracks
                stream.getTracks().forEach(track => track.stop());
                
                // Create audio blob
                const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                
                // Send to Whisper API
                await this.sendToWhisperAPI(audioBlob);
                
                this.isListening = false;
                this.updateUI('idle');
                
                if (this.config.onEnd) {
                    this.config.onEnd(this.finalTranscript);
                }
            };

            // Start recording
            this.mediaRecorder.start();
            
            // Auto-stop after max duration
            this.recordingTimeout = setTimeout(() => {
                if (this.isListening) {
                    this.stop();
                }
            }, this.config.whisperMaxDuration);
            
            return true;
            
        } catch (error) {
            console.error('Error accessing microphone:', error);
            this.isListening = false;
            this.updateUI('error');
            
            if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                this.showNotification('Microphone permission denied. Please allow microphone access.', 'error');
            } else if (error.name === 'NotFoundError') {
                this.showNotification('No microphone found. Please check your device.', 'error');
            } else {
                this.showNotification('Failed to access microphone: ' + error.message, 'error');
            }
            
            return false;
        }
    }

    /**
     * Send audio to Whisper API for transcription
     */
    async sendToWhisperAPI(audioBlob) {
        this.updateUI('processing');
        
        try {
            // Create form data
            const formData = new FormData();
            formData.append('audio', audioBlob, 'recording.webm');
            formData.append('language', this.config.language.split('-')[0]); // Convert en-US to en
            
            // Send to server
            const response = await fetch(this.config.whisperEndpoint, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                throw new Error(`Server returned ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();
            
            if (data.text) {
                this.finalTranscript = data.text;
                this.updateInputElement(data.text);
                
                if (this.config.onResult) {
                    this.config.onResult({
                        final: data.text,
                        interim: '',
                        full: data.text
                    });
                }
                
                this.showNotification('Transcription complete', 'success');
            } else if (data.error) {
                throw new Error(data.error);
            }
            
        } catch (error) {
            console.error('Error transcribing audio:', error);
            this.showNotification('Failed to transcribe audio: ' + error.message, 'error');
            
            if (this.config.onError) {
                this.config.onError('api-error', error.message);
            }
        }
    }

    /**
     * Stop listening
     */
    stop() {
        if (this.recordingTimeout) {
            clearTimeout(this.recordingTimeout);
            this.recordingTimeout = null;
        }

        if (this.mode === 'webspeech' && this.recognition && this.isListening) {
            this.recognition.stop();
        } else if (this.mode === 'whisper' && this.mediaRecorder && this.isListening) {
            this.mediaRecorder.stop();
        }
    }

    /**
     * Toggle listening state
     */
    toggle() {
        if (this.isListening) {
            this.stop();
        } else {
            this.start();
        }
    }

    /**
     * Update the input element with transcript
     */
    updateInputElement(text) {
        const inputElement = document.getElementById(this.config.inputElementId);
        if (inputElement) {
            inputElement.value = text;
            inputElement.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }

    /**
     * Update UI based on state
     */
    updateUI(state) {
        const button = document.getElementById(this.config.buttonElementId);
        if (!button) return;

        // Remove all state classes
        button.classList.remove('listening', 'processing', 'error', 'idle');
        
        // Add current state class
        button.classList.add(state);

        // Update button icon/text
        const icon = button.querySelector('i') || button;
        
        switch(state) {
            case 'listening':
                icon.classList.remove('fa-microphone', 'fa-spinner');
                icon.classList.add('fa-stop');
                button.setAttribute('title', 'Stop recording');
                break;
            case 'processing':
                icon.classList.remove('fa-microphone', 'fa-stop');
                icon.classList.add('fa-spinner', 'fa-spin');
                button.setAttribute('title', 'Processing...');
                button.disabled = true;
                break;
            case 'error':
            case 'idle':
                icon.classList.remove('fa-stop', 'fa-spinner', 'fa-spin');
                icon.classList.add('fa-microphone');
                button.setAttribute('title', 'Start voice input');
                button.disabled = false;
                break;
        }
    }

    /**
     * Get user-friendly error message
     */
    getErrorMessage(error) {
        const errorMessages = {
            'no-speech': 'No speech detected. Please try again.',
            'audio-capture': 'No microphone found. Please check your device.',
            'not-allowed': 'Microphone permission denied. Please allow microphone access.',
            'network': 'Network error. Please check your connection.',
            'aborted': 'Speech recognition aborted.',
            'service-not-allowed': 'Speech recognition service is not allowed.',
            'api-error': 'Failed to process audio. Please try again.'
        };

        return errorMessages[error] || 'An error occurred with speech recognition.';
    }

    /**
     * Show notification to user
     */
    showNotification(message, type = 'info') {
        // Check if Bootstrap toast container exists
        let toastContainer = document.getElementById('toast-container');
        
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toast-container';
            toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
            toastContainer.style.zIndex = '9999';
            document.body.appendChild(toastContainer);
        }

        const toastId = 'toast-' + Date.now();
        const bgClass = type === 'error' ? 'bg-danger' : type === 'warning' ? 'bg-warning' : type === 'success' ? 'bg-success' : 'bg-info';
        
        const toastHTML = `
            <div id="${toastId}" class="toast align-items-center text-white ${bgClass} border-0" role="alert" aria-live="assertive" aria-atomic="true">
                <div class="d-flex">
                    <div class="toast-body">
                        ${message}
                    </div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
                </div>
            </div>
        `;
        
        toastContainer.insertAdjacentHTML('beforeend', toastHTML);
        
        const toastElement = document.getElementById(toastId);
        const toast = new bootstrap.Toast(toastElement, { delay: 3000 });
        toast.show();
        
        // Remove from DOM after hidden
        toastElement.addEventListener('hidden.bs.toast', () => {
            toastElement.remove();
        });
    }

    /**
     * Change language
     */
    setLanguage(language) {
        this.config.language = language;
        if (this.recognition) {
            this.recognition.lang = language;
        }
    }

    /**
     * Get current status
     */
    getStatus() {
        return {
            isSupported: this.isSupported,
            isListening: this.isListening,
            language: this.config.language,
            mode: this.mode
        };
    }

    /**
     * Cleanup
     */
    destroy() {
        if (this.recordingTimeout) {
            clearTimeout(this.recordingTimeout);
        }
        
        if (this.recognition) {
            this.stop();
            this.recognition = null;
        }
        
        if (this.mediaRecorder) {
            this.stop();
            this.mediaRecorder = null;
        }
    }
}

// Export for use in modules or make globally available
if (typeof module !== 'undefined' && module.exports) {
    module.exports = SpeechRecognitionManager;
}
