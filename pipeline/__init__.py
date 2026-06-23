"""Pipeline: pure functions on bpy objects, callable from the operator or headless.

Each step takes (obj, settings, context) and mutates `obj` in place. `convert`
orchestrates them and owns the clone + scene-state + cleanup contract.
"""
