"""개발용: 용어 힌트 측정 표본(영어 합성음)을 만듭니다(R4).

분야 공통 용어가 든 일반적인 영어 문장을 합성음으로 읽혀 한 트랙에 이어 붙입니다. 실제 회의 내용이 아닙니다.
문장마다 (합성기에 읽힐 말, 기록에 나와야 할 표기) 를 둡니다. 약어는 사람이 읽는 방식대로 철자를 띄어 읽힙니다.

필요한 것: tools/make_sample.py 와 같은 음성 모델(piper en_US-libritts_r-medium, sherpa-onnx 1.12.x).
사용: python tools/make_term_sample.py <음성모델 폴더> <출력 폴더>
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_sample import OUT_SR, resample  # noqa: E402

VOICES = [148, 259, 296, 37, 518]
# (읽힐 말, [기록에 나와야 할 표기])
TERM_SENTENCES = [
    ("The C P K for the welding line is one point three three this week.", ["Cpk"]),
    ("Our P P K dropped after the supplier changed the material.", ["Ppk"]),
    ("We need a gauge R and R study before we release the fixture.", ["Gage R&R"]),
    ("Please update the D F M E A with the new failure mode.", ["DFMEA"]),
    ("The P F M E A review is scheduled for Thursday.", ["PFMEA"]),
    ("The customer asked for the full P-PAP package by the end of the month.", ["PPAP"]),
    ("Can you send me the P S W for the second shipment?", ["PSW"]),
    ("We are preparing for the I A T F sixteen nine four nine audit.", ["IATF 16949"]),
    ("The supplier submitted an eight D report for the leak issue.", ["8D"]),
    ("The D C I R of the module is higher than the target.", ["DCIR"]),
    ("Check the O C V of every cell before assembly.", ["OCV"]),
    ("The test starts at fifty percent S O C.", ["SOC"]),
    ("The S O H estimate in the B M S looks too optimistic.", ["SOH", "BMS"]),
    ("We ran the discharge test at a two C C-rate.", ["C-rate"]),
    ("The high-pot test failed on two units yesterday.", ["HiPot"]),
    ("The busbar temperature went up during the fast charge.", ["busbar"]),
    ("The F P C B connector is loose on the sample.", ["FPCB"]),
    ("We need to confirm the transport test under U N thirty-eight point three.", ["UN 38.3"]),
    ("The cell was tested according to I E C sixty-two six sixty.", ["IEC 62660"]),
    ("The module must pass U L twenty-five eighty before launch.", ["UL 2580"]),
    ("Thermal runaway was not observed in the nail penetration test.", ["thermal runaway", "nail penetration"]),
    ("We will add a gap filler between the module and the cooling plate.", ["gap filler", "cooling plate"]),
    ("The compression pad thickness changed in the new design.", ["compression pad"]),
    ("The pouch cell showed some swelling after the cycle test.", ["pouch cell", "swelling"]),
    ("Laser welding parameters were adjusted on line two.", ["laser welding"]),
    ("Please check the upper control limit, the U C L, on the chart.", ["UCL"]),
    ("The X bar R chart shows a shift in the process mean.", ["X-bar R chart"]),
    ("We added a poka-yoke to prevent reversed connectors.", ["poka-yoke"]),
    ("The C T Q characteristics are listed in the drawing.", ["CTQ"]),
    ("We opened an N C R and a corrective action, a C A P A, for that lot.", ["NCR", "CAPA"]),
    ("The O E E of the assembly line was eighty two percent.", ["OEE"]),
    ("First pass yield, F P Y, improved after the fixture change.", ["FPY"]),
    ("The E O L tester rejected three modules today.", ["EOL"]),
    ("The N M C eight one one cells have higher energy density.", ["NMC811"]),
    ("We switched to L F P for the entry model.", ["LFP"]),
    ("The D O E results will be analyzed with an A N O V A.", ["DOE", "ANOVA"]),
    ("The C M M measurement confirmed the flatness of the end plate.", ["CMM", "end plate"]),
    ("Ultrasonic wire bonding is used for the cell connection.", ["ultrasonic wire bonding"]),
    ("The thermal propagation test is planned for next quarter.", ["thermal propagation"]),
    ("The prismatic cell supplier will visit us next week.", ["prismatic cell"]),
]


def main(model_dir: str, out_dir: str) -> None:
    import sherpa_onnx as so

    d = Path(model_dir)
    tts = so.OfflineTts(so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(
        vits=so.OfflineTtsVitsModelConfig(model=str(d / "en_US-libritts_r-medium.onnx"),
                                          tokens=str(d / "tokens.txt"), data_dir=str(d / "espeak-ng-data")),
        num_threads=4)))
    t, clips, truth = 0.5, [], []
    for i, (spoken, terms) in enumerate(TERM_SENTENCES):
        a = tts.generate(spoken, sid=VOICES[i % len(VOICES)], speed=1.0)
        x = resample(np.asarray(a.samples, dtype=np.float32), a.sample_rate, OUT_SR)
        x = x / max(1e-6, np.abs(x).max()) * 0.5
        clips.append((t, x))
        truth.append({"start": round(t, 2), "end": round(t + len(x) / OUT_SR, 2), "spoken": spoken, "terms": terms})
        t += len(x) / OUT_SR + 0.8
    y = np.zeros(int((t + 0.5) * OUT_SR), np.float32)
    for s, x in clips:
        y[int(s * OUT_SR):int(s * OUT_SR) + len(x)] += x
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sf.write(out / "term_sentences.flac", y, OUT_SR, subtype="PCM_16")
    (out / "term_sentences_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(truth)} sentences, {len(y) / OUT_SR:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
