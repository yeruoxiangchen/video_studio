# C++ batch frame sampler

`camcat-native-media` uses FFmpeg's `libavformat`, `libavcodec` and `libswscale`
directly, exposing RGB NumPy arrays through pybind11. The production source
analysis and narrative sampler use it when `CAMCAT_MEDIA_FRAME_BACKEND=native`.
Compose builds the extension in a separate stage and selects this backend by default.

Each batch owns its demuxer, decoder and scaler. Timestamps are normalized using
the stream time base; requests are sorted internally and restored to caller order.
Variable frame rate, nonzero stream starts and right-angle display rotations are
handled without assuming `frame_index / fps`. The decoder releases the Python GIL;
returned arrays retain independent C++ buffers through owner capsules. FFmpeg
contexts use RAII. Decoding has bounded threads, time, dimensions and output memory.
File extraction calls are split into batches of at most 16 frames.

## Build and run

Docker installs build headers only in the native build stage; runtime needs FFmpeg
shared libraries. For a Python 3.12 host development environment:

```bash
# Install the distribution's compiler, CMake, pkg-config and FFmpeg development
# headers through the machine's normal package management process.
python -m pip install ./native/media
CAMCAT_MEDIA_FRAME_BACKEND=native python -m camcat.worker
```

`CAMCAT_MEDIA_FRAME_BACKEND=ffmpeg` explicitly selects the previous CLI path.
A missing native extension raises an installation error; there is no implicit switch.
Use the same backend in the deployment and in its measurements.

Real media checks: `tests/integration/test_native_media_real.py`. They cover timestamp
order, variable frame rate, rotation, invalid input, concurrent ownership and chunks.
`scripts/benchmark_native_media.py` compares fresh processes on the same real input,
records JPEG output differences and includes decoder library versions.

## Implementation references

The implementation follows FFmpeg's documented send/receive API and ownership rules:

- [FFmpeg demuxing/decoding example](https://ffmpeg.org/doxygen/5.1/demuxing_decoding_8c-example.html)
- [FFmpeg send/receive API](https://ffmpeg.org/doxygen/5.1/group__lavc__encdec.html)
- [Display transformation matrix](https://ffmpeg.org/doxygen/5.1/group__lavu__video__display.html)
- [pybind11 NumPy buffers](https://pybind11.readthedocs.io/en/stable/advanced/pycpp/numpy.html)

The decoder state, batching, bounds and Python adapter are project code; no fork of
FFmpeg or pybind11 is embedded. FFmpeg and pybind11 retain their upstream licenses.
