# Local image OCR baseline

On 2026-11-04, Tesseract 5.5.3 with Leptonica 1.87 on macOS 27.2 arm64 recovered both Chinese lines from the original regression image. Its 1100 × 210 canvas contained all visible ink within (30, 28)–(408, 139). The original uses 44 px STHeiti Medium. At the same resolution and with identical image and model bytes, automatic page segmentation (PSM 3) returned fragments; single-block PSM 6 recovered both sentences. Single-line PSM 7 returned empty on this two-line image. PSM 11 recovered both lines separated by a blank line. This reproduces the reported symptom and isolates a sufficient configuration change on this host.

Use this command for **clear, horizontal, single-column** PNG/JPEG study text:

```sh
tesseract image.png stdout -l chi_sim+eng --psm 6 --oem 1 -c textord_noise_rejrows=0
```

The row-rejection override fixes a second problem observed during development. On a clear 32 px Chinese fixture, stock PSM 6 lost the second line. Changing only `textord_noise_rejrows` recovered it. This setting can also retain unwanted marks. The user must inspect the original OCR text before synthesis. Two-column, rotated and low-resolution pages need special attention.

The installed models match the Git blob identities of the official [fast models](https://github.com/tesseract-ocr/tessdata_fast). The Chinese file is 2,469,156 bytes, blob `388bac276d033d06e5ed5ba7a7ad14ae58f97dab`; English is 4,113,088 bytes, blob `bbef4675053b5b468cdb477053e28b1c698ba08e`. The available language data did not match tessdata_best. The experiment fetched metadata and did not download any weights. No comparison of best-model accuracy was performed.

## Reproduce the golden set

Use a separate benchmark environment with Pillow (tested 12.3). Pillow generates fixtures; it is optional for this script. Supply installed Chinese fonts with redistribution rights or use the local macOS system fonts without copying font files. Select a new directory on each run.

```sh
python scripts/ocr_baseline.py \
  --output .artifacts/ocr-baseline-new \
  --font '/System/Library/Fonts/STHeiti Medium.ttc' \
  --second-font '/System/Library/Fonts/STHeiti Light.ttc' \
  --tesseract tesseract
```

The runner writes 54 self-authored PNG/JPG/JPEG fixtures plus reference lines, commands, input hashes, stdout/stderr, elapsed time and metrics. Images vary in margin, indent, font weight (Medium/Light), font size (32/40/48 px) and format. The first four triples (12 images) were used for development; the next eight triples (24 images) were seen in the initial comparison and count as validation. The final six triples (18 images) were generated after selecting the final parameters for a fresh holdout. Use `--default-row-rejection` for a controlled A/B comparison. Recognition does not use the expected strings.

| Clear single-column images | Images | Raw CER | Normalized CER | Lines recalled | Images complete in order | Total wall time |
|---|---:|---:|---:|---:|---:|---:|
| Chinese | 18 | 14/327 = 4.28% | 1/309 = 0.32% | 36/36 | 18/18 | 2.26 s |
| English | 18 | 0/914 | 0/765 | 36/36 | 18/18 | 2.38 s |
| Mixed | 18 | 24/1083 = 2.22% | 15/916 = 1.64% | 52/54 | 16/18 | 3.05 s |

Fresh holdout: Chinese raw CER 4.72%, normalized CER 0%, 12/12 lines; English both CERs 0%, 12/12 lines; mixed raw CER 2.58%, normalized CER 1.71%, 17/18 lines. The all-Chinese aggregate and held-out smoke gates passed (≤5% CER, no whole Chinese line missing). Two mixed images contain a badly misread Chinese line even though English appears correct. Review is required. Production must fail an explicitly Chinese request that produces no Chinese characters.

Raw CER counts Levenshtein insertions, substitutions and deletions against reference text including internal spaces/newlines, without terminal linefeeds. Normalized CER uses NFKC and removes whitespace from both strings. A line is recalled when a unique output line matches with edit distance at most 20% of the longer normalized line. The ordering check requires all source lines to be recalled in increasing output-line order. Output duplicates and extra characters are counted in CER. “Complete in order” measures line coverage; it does not require every character to match.

In separate stress checks, two columns read in the wrong order (25/53 normalized character edits), a 9° tilt produced 10/46 edits, and quarter-resolution text 12/46. Their performance is outside the clear-text gate. Scanned PDFs, vertical text, live screen capture and handwriting remain untested.

## Provider decision

Keep the input at its original resolution. On the original smoke fixture, grayscale, thresholding, border padding and 2×/3× resampling did not restore any additional lines after PSM 6 was selected. Scaling alone did not reliably fix the second fixture under Qt resampling. Use an external local Tesseract installation and verify requested language packs before recognizing. Do not substitute English for unsupported Chinese.

On the same macOS 27.2 host, Vision revision 3 in fast mode advertised six Western languages, none Chinese, and returned an empty result for the original pure-Chinese fixture. Accurate mode advertised Simplified Chinese, but its strictly configured helper exceeded the eight-second timeout. Report unsupported languages/timeout to the caller. The stable measured Chinese baseline is Tesseract. The Vision result does not establish behavior on other macOS versions.
