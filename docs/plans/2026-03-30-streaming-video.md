# Streaming Video Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver a real end-to-end live video stream for the Garage camera on branch `streming_video`, first in `pycloudedge`, then through `cloudedge-ha`.

**Architecture:** Keep `pycloudedge` as the proprietary P2P/HEVC extractor and keep `cloudedge-ha` as the Home Assistant bridge that remuxes raw HEVC into a local MPEG-TS `stream_source`. Validate the path incrementally: baseline tests, direct Garage streaming, then Home Assistant bridge integration.

**Tech Stack:** Python, pytest, CloudEdge OpenAPI + proprietary P2P signaling, HEVC, ffmpeg, Home Assistant camera `stream` integration.

### Task 1: Restore a trustworthy `pycloudedge` baseline

**Files:**
- Modify: `tests/test_basic.py`
- Test: `tests/test_basic.py::TestAsyncMethods::test_client_methods_with_mock`
- Test: `tests/test_basic.py`

**Step 1: Confirm the failing test is a real baseline regression**

Run: `.venv/bin/pytest tests/test_basic.py::TestAsyncMethods::test_client_methods_with_mock -v`
Expected: FAIL with `ValidationError: Invalid email format: test`

**Step 2: Apply the minimal fix in the test**

Replace the invalid username with a valid email literal already accepted by the constructor.

```python
client = CloudEdgeClient("test@example.com", "test", "US", "+1")
```

**Step 3: Re-run the targeted test**

Run: `.venv/bin/pytest tests/test_basic.py::TestAsyncMethods::test_client_methods_with_mock -v`
Expected: PASS

**Step 4: Re-run the full `pycloudedge` suite**

Run: `.venv/bin/pytest`
Expected: all tests pass, or only pre-existing warnings remain

### Task 2: Make direct Garage streaming reproducible from `pycloudedge`

**Files:**
- Modify: `tests/test_improvements.py` or create `tests/test_streaming_helpers.py`
- Modify if needed: `examples/stream_test.py`
- Modify if needed: `cloudedge/client.py`
- Modify if needed: `cloudedge/p2p/p2p_streamer.py`

**Step 1: Write a failing helper-level test for the smallest bug you need to fix**

Examples:
- Annex B conversion for one access unit
- Keyframe gating behavior
- signaling candidate ordering
- phone code normalization or env parsing if that blocks the real script

Use a focused unit test instead of mocking the whole live session.

**Step 2: Run the focused test and watch it fail**

Run: `.venv/bin/pytest <targeted test path> -v`
Expected: FAIL for the exact missing behavior

**Step 3: Implement the minimal code change**

Touch only the production function proved by the test.

**Step 4: Re-run the focused test and then the full suite**

Run:
- `.venv/bin/pytest <targeted test path> -v`
- `.venv/bin/pytest`

Expected: PASS

**Step 5: Validate against the real Garage camera**

Run in order:
- `.venv/bin/pip install -e .[examples,mqtt]`
- `.venv/bin/python examples/stream_test.py /Users/fdaloisio/mygit/pycloudedge/.env`

Success criteria:
- authentication succeeds
- Garage is selected
- stream login succeeds
- continuous video frames are received

If playback inspection is needed and the environment supports it:
- `.venv/bin/python examples/stream_test.py /Users/fdaloisio/mygit/pycloudedge/.env --ffplay`

### Task 3: Capture objective evidence that the live stream is real

**Files:**
- Create if needed: `examples/stream_record.py`
- Modify if needed: `examples/stream_test.py`

**Step 1: Write a failing test for any new helper introduced for recording/probing**

If you need a new helper to write frames to `ffmpeg`, test that helper first. Keep the test offline and deterministic.

**Step 2: Implement the smallest helper needed**

Examples:
- write Annex B frames to ffmpeg stdin
- stop after first valid keyframe plus N seconds
- emit a non-secret summary line for verification

**Step 3: Run a real recording/probe command**

Examples:
- `.venv/bin/python examples/stream_test.py /Users/fdaloisio/mygit/pycloudedge/.env`
- `ffmpeg -hide_banner -loglevel warning -f hevc -i <captured stream> -t 10 -f null -`
- `ffprobe <captured artifact>`

Expected: ffmpeg/ffprobe recognizes a valid video stream and reports video packets/frames.

### Task 4: Verify and harden the Home Assistant bridge path

**Files:**
- Modify if needed: `/Users/fdaloisio/.config/superpowers/worktrees/cloudedge-ha/streming_video/custom_components/cloudedge/stream_bridge.py`
- Modify if needed: `/Users/fdaloisio/.config/superpowers/worktrees/cloudedge-ha/streming_video/custom_components/cloudedge/camera.py`
- Modify if needed: `/Users/fdaloisio/.config/superpowers/worktrees/cloudedge-ha/streming_video/custom_components/cloudedge/__init__.py`

**Step 1: Write a failing unit test for the smallest HA-side bug you discover**

Focus on pure logic where possible:
- `stream_source()` behavior
- keyframe gating
- bootstrap keyframe handling
- bridge lifecycle / idle stop behavior

**Step 2: Run the targeted test and watch it fail**

Use the project’s chosen Python test command once test scaffolding exists.

**Step 3: Implement the minimal bridge fix**

Keep the architecture unchanged: `pycloudedge` provides raw frames, `cloudedge-ha` remuxes them and exposes `tcp://127.0.0.1:<port>`.

**Step 4: Validate the runtime path**

At minimum verify:
- `async_get_stream_source()` returns a local TCP source
- `ffmpeg` starts successfully
- the bridge accepts a client and emits MPEG-TS bytes

If a local HA harness must be created, keep it minimal and non-destructive.

### Task 5: Document and verify the finished streaming path

**Files:**
- Modify: `README.md` in `pycloudedge` if examples change
- Modify: `README.md` in `cloudedge-ha` if runtime requirements or caveats change
- Modify: `docs/plans/2026-03-30-streaming-video.md`

**Step 1: Re-run all relevant automated checks**

Run:
- `.venv/bin/pytest`
- any new targeted HA-side tests

**Step 2: Re-run the real Garage validation**

Run the final direct streaming command again and confirm frame/stream evidence still holds.

**Step 3: Update docs with the exact supported path**

Document:
- branch requirements
- local dependency requirements (`ffmpeg`, local `pycloudedge` build)
- what works now
- what remains unsupported, especially audio if still absent

**Step 4: Record final verification evidence**

Capture the exact commands run and the observed non-secret success signals so the work can be reproduced.
