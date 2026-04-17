"""
Minimal Whisper API Backend
Only provides /api/transcribe endpoint - integrate into your existing Flask app
"""

from flask import Blueprint, request, jsonify
from openai import OpenAI
import openai
import os
import tempfile
import logging
from logging.handlers import WatchedFileHandler
import config as cfg
from CommonUtils import rotate_logs_on_startup, get_log_path


# Create the blueprint
whisper_bp = Blueprint('whisper', __name__)

# Configure logging
def setup_logging():
    """Configure logging for the workflow execution"""
    logger = logging.getLogger("Whisper")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('WHISPER_LOG', get_log_path('whisper_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger

rotate_logs_on_startup(os.getenv('WHISPER_LOG', get_log_path('whisper_log.txt')))

logger = setup_logging()

# Lazy-initialized OpenAI client
_whisper_client = None

def _get_whisper_client():
    """Lazy-initialize and return the OpenAI client for Whisper."""
    global _whisper_client
    if _whisper_client is None:
        api_key = cfg.OPENAI_API_KEY
        if not api_key:
            logger.warning("OPENAI_API_KEY not set!")
            return None
        _whisper_client = OpenAI(api_key=api_key)
    return _whisper_client


@whisper_bp.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    """
    Transcribe audio using OpenAI Whisper API

    Expects:
        - audio file in request.files['audio']
        - optional language in request.form['language']

    Returns:
        - JSON: {'text': 'transcribed text', 'language': 'en'}
    """
    try:
        client = _get_whisper_client()
        if client is None:
            logger.error("OpenAI API key not configured")
            return jsonify({
                'error': 'Speech recognition service not configured'
            }), 500

        # Check if audio file was provided
        if 'audio' not in request.files:
            return jsonify({'error': 'No audio file provided'}), 400

        audio_file = request.files['audio']

        if audio_file.filename == '':
            return jsonify({'error': 'No audio file selected'}), 400

        # Get language parameter (optional)
        language = request.form.get('language', None)

        # Save audio to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.webm') as temp_audio:
            audio_file.save(temp_audio.name)
            temp_audio_path = temp_audio.name

        try:
            # Open the file for Whisper API
            with open(temp_audio_path, 'rb') as audio_fp:
                logger.info(f"Transcribing audio...")

                # Build transcription params
                transcription_params = {
                    'model': 'whisper-1',
                    'file': audio_fp
                }

                # Add language if specified
                if language:
                    transcription_params['language'] = language

                # Make API call (v1.x SDK)
                transcript = client.audio.transcriptions.create(**transcription_params)

                logger.info(f"Transcription successful: {transcript.text[:50]}...")

                return jsonify({
                    'text': transcript.text,
                    'language': language
                })

        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_audio_path)
            except Exception as e:
                logger.warning(f"Failed to delete temporary file: {e}")

    except openai.AuthenticationError:
        logger.error("OpenAI API authentication failed")
        return jsonify({
            'error': 'API authentication failed'
        }), 500

    except openai.RateLimitError:
        logger.error("OpenAI API rate limit exceeded")
        return jsonify({
            'error': 'Rate limit exceeded. Please try again in a moment.'
        }), 429

    except openai.APIError as e:
        logger.error(f"OpenAI API error: {str(e)}")
        return jsonify({
            'error': f'API error: {str(e)}'
        }), 500

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({
            'error': f'An unexpected error occurred'
        }), 500
