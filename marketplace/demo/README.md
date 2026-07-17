# Quick Start Demo

Generate `cloth_next_quickstart.blend` with the repository script:

```powershell
blender --background --python tools/create_superhive_demo.py -- --output marketplace/demo/cloth_next_quickstart.blend
```

Open the generated file in every Blender version advertised on Superhive. Run
Scene Health Check and a short Bake on a clean test account before uploading it.
The `.blend` is a separate marketplace file and must not be inserted into the
Blender extension ZIP.

Validate its persistent roles, naming, linked data, and 2:1 render setup with:

```powershell
blender --background marketplace/demo/cloth_next_quickstart.blend --python-exit-code 1 --python tools/validate_superhive_demo.py
```
