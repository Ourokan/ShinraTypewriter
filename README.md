# Shinra Typewriter

An auto-typer for the FFXIV **Keyboard Square** typing event. It watches your screen,
reads the word boxes above the enemies, and types them for you, pressing Enter after each one.

## Download

Grab `ShinraTypewriter.exe` from the [Releases](../../releases) page. It's a single file
with everything built in (Python, OpenCV and the Tesseract OCR engine), so there's nothing
else to install.

## Using it

1. Run the game in **Windowed** or **Borderless Windowed** mode.
2. Open `ShinraTypewriter.exe`. If Windows shows "Windows protected your PC", click
   **More info → Run anyway** (the exe isn't code-signed).
3. Pick the screen the game is on.
4. Click into the game and press **Ctrl+Alt+S** to start. Press it again to stop.

It only types while the FFXIV window is active, so clicking into another window pauses it.

| Hotkey | Action |
|---|---|
| Ctrl+Alt+S | Start / stop |
| Ctrl+Alt+D | Debug snapshot: a picture showing what it will type (green) and what it skipped (red, with the reason) |
| Ctrl+Alt+P | Save a screenshot |

Settings (typing speed, pause before Enter, green/red priority, capitals, repeat block) are
in the window and saved to `Documents\Shinra Typewriter`.

## How it works

- **Finds the boxes by shape, not colour.** Rounded rectangles with evenly padded text are
  detected from edges. Outlines broken up by spell effects are stitched back together, and
  green/red boxes are also found by their fill colour.
- **Only types the box in front.** A box partly hidden behind another doesn't have even
  padding around its text, so it's skipped until it's uncovered.
- **Reads carefully.** Each box is read with Tesseract. Unsure readings are re-read at
  another size and must agree, and must repeat across two scans before being typed. Common
  OCR slips like Roman numerals ("Xiil" → "XIII") are corrected.
- **Priority:** green boxes first, then red, then the rest, closest enemies first.
- Waits while the input bar shows **Inputs disabled!**, and won't retype the same word
  within 2 seconds.

Detection was tuned on 3440×1440 screenshots and scales with screen height.

## Building from source

Needs Windows, Python 3.10+ and [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)
installed in its default location. Then run:

```
build.bat
```

It creates a virtual environment, installs the packages in `requirements.txt`, bundles
Tesseract's English data, and produces `ShinraTypewriter.exe` with PyInstaller.

To run without building: `pip install -r requirements.txt` then `python app.py`.

## Credits

- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (Apache 2.0), bundled in the exe
- [OpenCV](https://opencv.org/), [mss](https://github.com/BoboTiG/python-mss),
  [pytesseract](https://github.com/madmaze/pytesseract),
  [PyDirectInput](https://github.com/learncodebygaming/pydirectinput),
  [keyboard](https://github.com/boppreh/keyboard)

## Disclaimer

Automating gameplay is against the FINAL FANTASY XIV terms of service. Use at your own risk.
Not affiliated with Square Enix.
