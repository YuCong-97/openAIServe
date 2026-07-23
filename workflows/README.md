# ComfyUI Video Workflows

`/v1/videos/generations` uses the built-in Wan2.1 T2V 1.3B workflow by default.
Use this folder only when you want to switch to a custom ComfyUI API-format workflow JSON.

1. Build and test the video workflow in ComfyUI.
2. Export the API workflow JSON.
3. Replace values you want the server to fill with tokens such as `{{prompt}}`, `{{negative_prompt}}`, `{{seed}}`, `{{width}}`, `{{height}}`, `{{frames}}`, and `{{steps}}`.
4. For video LoRA slots, use tokens such as `{{lora_0_name}}`, `{{lora_0_strength_model}}`, `{{lora_1_name}}`, and `{{lora_1_strength_model}}` in `LoraLoaderModelOnly` or equivalent video LoRA nodes.
5. Save it in this folder and set `providers.comfyui.video.workflow_template` in `config.yaml`.

Different ComfyUI video stacks use different custom nodes and model folders, so this project keeps video generation workflow-driven instead of hard-coding one node graph.
