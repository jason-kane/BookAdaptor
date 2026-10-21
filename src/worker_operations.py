"""ML implementations. Import this module only in the video worker."""
import base64
from functools import lru_cache

OPERATIONS = frozenset({"sentences", "whisper_ipa", "t5_tokens"})


@lru_cache(maxsize=1)
def sentence_model():
    import spacy
    spacy.prefer_gpu()
    return spacy.load("en_core_web_sm")


@lru_cache(maxsize=1)
def whisper_model():
    import torch
    from transformers import WhisperProcessor, WhisperForConditionalGeneration, WhisperFeatureExtractor
    name = "neurlang/ipa-whisper-medium"
    model = WhisperForConditionalGeneration.from_pretrained(name)
    model.config.forced_decoder_ids = None
    model.config.suppress_tokens = []
    model.generation_config.forced_decoder_ids = None
    model.generation_config._from_model_config = True
    model.to("cuda" if torch.cuda.is_available() else "cpu").eval()
    return WhisperProcessor.from_pretrained(name), model, WhisperFeatureExtractor.from_pretrained(name)


@lru_cache(maxsize=1)
def tokenizer():
    from transformers import T5Tokenizer
    return T5Tokenizer.from_pretrained("t5-small")


def execute(operation, arguments):
    if operation == "sentences":
        return [{"text": sent.text, "text_with_ws": sent.text_with_ws}
                for sent in sentence_model()(arguments["text"]).sents]
    if operation == "t5_tokens":
        return tokenizer().tokenize(arguments["text"])
    if operation == "whisper_ipa":
        import numpy as np
        import torch
        processor, model, extractor = whisper_model()
        audio = base64.b64decode(arguments["audio"], validate=True)[:30 * 16000 * 2]
        samples = np.frombuffer(audio, np.int16).astype(np.float32) / 32768.0
        features = extractor(raw_speech=samples, sampling_rate=16000, return_tensors="pt").input_features.to(model.device)
        with torch.inference_mode():
            predicted = model.generate(input_features=features)
        return processor.batch_decode(predicted, skip_special_tokens=True)
    raise ValueError(f"Unknown worker operation: {operation}")
