from transformers import AutoModel


def load_encoder(model_name: str):
    return AutoModel.from_pretrained(model_name)

