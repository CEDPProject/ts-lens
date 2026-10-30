"""Serve an already-installed transformers causal language model on CPU only.

No downloading, remote model code, GPU allocation or model identity substitution.
"""
import argparse
from pathlib import Path
import threading

from flask import Flask, jsonify, request


def create_app(model, tokenizer):
    import torch
    app = Flask(__name__)
    lock = threading.Lock()

    @app.get('/v1/models')
    def models():
        return jsonify(data=[{'id': 'local-cpu-llm'}], device='cpu')

    @app.post('/v1/chat/completions')
    def chat():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error='JSON body must be an object'), 400
        messages = data.get('messages')
        if not isinstance(messages, list) or not messages:
            return jsonify(error='messages must be a nonempty array'), 400
        if any(not isinstance(message, dict)
               or message.get('role') not in {'system', 'user', 'assistant'}
               or not isinstance(message.get('content'), str) for message in messages):
            return jsonify(error='messages require a valid role and string content'), 400
        try:
            budget = max(1, min(2048, int(data.get('max_tokens', 512))))
            with lock, torch.inference_mode():
                prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                inputs = tokenizer(prompt, return_tensors='pt')
                limit = int(getattr(model.config, 'max_position_embeddings', 4096))
                if inputs['input_ids'].shape[1] + budget > limit:
                    return jsonify(error='Prompt exceeds local CPU model context length'), 400
                output = model.generate(**inputs, max_new_tokens=budget, do_sample=False,
                                        pad_token_id=tokenizer.eos_token_id)
                text = tokenizer.decode(output[0, inputs['input_ids'].shape[1]:], skip_special_tokens=True)
            return jsonify(model='local-cpu-llm', device='cpu', choices=[{'message': {'role': 'assistant', 'content': text}}])
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error)), 400
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--port', type=int, default=7013)
    args = parser.parse_args()
    if not args.model.is_dir():
        parser.error('Prepare a local model directory first')
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(args.model, local_files_only=True,
        trust_remote_code=False, torch_dtype=torch.float32).to('cpu').eval()
    create_app(model, tokenizer).run(host='127.0.0.1', port=args.port, threaded=True)


if __name__ == '__main__':
    main()
