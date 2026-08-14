# Third-party notices

## ReClip source baseline

Portions of LinkSift are derived from [ReClip](https://github.com/averygan/reclip) at commit [`1d161d15a4fe93d9b3371377f0a421dc3e965b10`](https://github.com/averygan/reclip/commit/1d161d15a4fe93d9b3371377f0a421dc3e965b10). The upstream license notice at that revision is reproduced below.

```text
MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Bundled and optional runtime components

The container images bundle or optionally install the following third-party components. Each retains its own license; none of them changes the MIT licensing of the LinkSift source itself.

- [Deno](https://github.com/denoland/deno) (MIT License) — JavaScript runtime copied from the official `denoland/deno:bin` image into the default LinkSift image so yt-dlp can solve YouTube JS challenges.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense) — downloader used by LinkSift, installed in the default image and updated at container startup.
- [yt-dlp-ejs](https://github.com/yt-dlp/ejs) (Unlicense) — external JavaScript challenge solver scripts for yt-dlp, installed in the default image and updated at container startup.
- [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) (GPL-3.0) — optional PO token provider plugin and sidecar server. It is **not** part of the default image; it is only installed in the separate `youtube-robust` build target and runs as its own container when the robust Compose overlay is used.

See [PROVENANCE.md](PROVENANCE.md) for the baseline audit and the boundary between inherited source and independent LinkSift work. Runtime dependencies and the base container image retain their respective licenses; refer to their installed package metadata and upstream distributions for those notices.
