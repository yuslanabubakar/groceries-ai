import os
import wave
from pathlib import Path
from pydub import AudioSegment
from google.cloud import speech
from google.oauth2 import service_account

class SpeechToText:
    """
    A class to handle audio transcription using Google Cloud Speech-to-Text API.
    
    This class manages authentication, audio file conversion to WAV format,
    and sending requests to the Google Cloud API to get transcriptions.
    """

    def __init__(self, service_account_path: str):
        """
        Initializes the SpeechToText client.

        Args:
            service_account_path (str): The file path to the Google Cloud service account JSON key.
        """
        try:
            credentials = service_account.Credentials.from_service_account_file(service_account_path)
            self.client = speech.SpeechClient(credentials=credentials)
            print("SpeechToText client initialized successfully.")
        except Exception as e:
            print(f"Error initializing client with service account from {service_account_path}: {e}")
            raise

    def _convert_to_wav(self, input_path: Path) -> (Path, int):
        """
        Converts an audio file to a mono, 16-bit WAV file, which is optimal for the API.

        Args:
            input_path (Path): The path to the input audio file (e.g., .m4a, .mp3).

        Returns:
            A tuple containing:
            - wav_path (Path): The path to the converted WAV file.
            - sample_rate (int): The sample rate of the converted WAV file.
        """
        wav_path = input_path.with_suffix('.wav')
        print(f"Input file: {input_path}")
        
        try:
            print("Loading audio file with pydub...")
            audio = AudioSegment.from_file(input_path)
            
            print(f"Exporting to WAV format: {wav_path}")
            # Export as WAV, ensuring mono channel and 16-bit PCM codec
            audio.export(wav_path, format="wav", parameters=["-ac", "1", "-acodec", "pcm_s16le"])

            # Get sample rate from the newly created file
            with wave.open(str(wav_path), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
            
            print(f"Detected sample rate of converted WAV: {sample_rate} Hz")
            return wav_path, sample_rate

        except Exception as e:
            print(f"Error during audio conversion: {e}")
            raise

    def transcribe_audio(self, input_file_path: str, language_code: str = "id-ID", cleanup_wav: bool = True) -> list:
        """
        Transcribes a given audio file.

        Args:
            input_file_path (str): The path to the audio file to transcribe.
            language_code (str, optional): The language code for transcription (e.g., "en-US"). Defaults to "id-ID".
            cleanup_wav (bool, optional): If True, deletes the temporary WAV file after transcription. Defaults to True.

        Returns:
            list: A list of transcription results from the API. Returns an empty list on failure.
        """
        input_path = Path(input_file_path)
        if not input_path.exists():
            print(f"Error: Input file not found at {input_file_path}")
            return []

        wav_path = None
        try:
            # Convert audio and get its properties
            wav_path, sample_rate = self._convert_to_wav(input_path)

            # Read audio content for the API
            with open(wav_path, "rb") as audio_file:
                content = audio_file.read()
            
            recognition_audio = speech.RecognitionAudio(content=content)

            # Configure and send the recognition request
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
                language_code=language_code,
                alternative_language_codes=["en-US"], # Example of alternative
            )

            print("Sending request to Speech-to-Text API...")
            response = self.client.recognize(config=config, audio=recognition_audio)
            
            return response.results

        except Exception as e:
            print(f"An error occurred during transcription: {e}")
            return []
        finally:
            # Clean up the temporary WAV file
            if cleanup_wav and wav_path and wav_path.exists():
                os.remove(wav_path)
                print(f"Cleaned up temporary file: {wav_path}")