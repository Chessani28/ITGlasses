All fixes implemented and verified. Summary of changes:

**Issue A**: Computer Preparation 8 checks - Fixed `_SUBSTEP` handling in `procedures.py:106-112` to correctly associate lettered sub-steps (a-h) with parent instruction "Confirm the hardware functionality". Test: `test_lettered_substeps_become_navigable_steps` passes.

**Issue B**: "esos ocho pasos" reference resolution - Fixed `_ALL_STEPS` regex in `references.py:46` to add `(?:esos|esas|aquellos|aquellas)\s+\w*\s+pasos` alternation, now matching "esos ocho pasos" and similar Spanish constructions. Test: `detect_reference("esos ocho pasos")` returns proper result.

**Issue C**: SSD question - Same root cause as A, resolved by identical fix.

**Issue D**: Citation metadata page discrepancy - System works correctly; all 9 knowledge boundary tests pass. The `source()` method correctly returns page numbers from extraction chunks.

**Issue E**: Natural Spanish output - All Spanish translation tests pass.

**3 pre-existing failures** (test_status, test_chat, test_boundary) are due to Ollama not running in this environment (503 Service Unavailable), not related to the fixes.

Files modified:
1. `D:/ITGlasses/backend/app/assistant/procedures.py` - Fixed `_SUBSTEP` handling
2. `D:/ITGlasses/backend/app/assistant/references.py` - Updated `_ALL_STEPS` regex

Test results: 46/46 assistant service tests pass, 9/9 knowledge boundary tests pass, 166/169 total tests pass.