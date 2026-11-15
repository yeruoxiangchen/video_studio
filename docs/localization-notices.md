# Sources and third-party components

| Component | Source / revision | Use |
| --- | --- | --- |
| CamCat | https://github.com/Kehan-Peng/CamCat-Agentic-Cut-Platform / `1e8d0492a9793f47d9a69362378fb2535f386b52` | Actual base repository, MIT. Original history and LICENSE preserved. |
| VideoLingo | https://github.com/Huanshere/VideoLingo | Reference for contextual translation and duration-aware dubbing. No code copied into this extension. |
| Piper | https://github.com/OHF-Voice/piper1-gpl / `piper-tts==1.3.0` | Optional separate CPU speech provider, GPL-3.0 distribution. |
| lessac medium | https://huggingface.co/rhasspy/piper-voices/tree/b145f2ac26522d6a2ccde0164b7b0e48b1e3199c/en/en_US/lessac/medium | English voice; original MODEL_CARD and its dataset/license links retained beside weights. |
| FFmpeg | https://ffmpeg.org/ | Media normalization and native rendering. Imported binaries retain GPLv3.txt and original readme in the data directory. |
| Noto CJK | https://github.com/notofonts/noto-cjk | Imported font retains supplied LICENSE; Compose uses packaged Noto CJK. |

Runtime/model files and receipts live under `/data/zjr/camcat_studio/`, outside source control.
CamCat's original provider references and media-license manifest remain applicable.
