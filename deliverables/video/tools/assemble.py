"""Build the final video: clip normalisation, crossfades, narration placement, lower-third captions, SRT."""
import asyncio
import json
import re
import subprocess
import urllib.parse
from pathlib import Path

from cdp import Browser
from script import SCENES, caption_chunks

FF = open("/tmp/hpvid/ff").read().strip()
T = json.load(open("../audio/timing.json"))
D = {s["id"]: s["dur"] for s in T["scenes"]}
GAP, LEAD, TAIL, X = T["gap"], T["lead"], T["tail"], 0.4
FPS = 30
OUT = Path("..")
TMP = Path("/tmp/hpvid/asm")
TMP.mkdir(parents=True, exist_ok=True)
APP = {"s04_warned", "s05_replay", "s06_brief", "s07_vietnam", "s08_sandbox", "s09_ops"}
LOWER = Path("cards/lower.html").resolve().as_uri()


def run(args):
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


ids = [s["id"] for s in SCENES]
# narration start per scene, visual start = narration start (clips were recorded against narration t=0)
A = []
t = LEAD
for i in ids:
    A.append(t)
    t += D[i] + GAP
END = A[-1] + D[ids[-1]] + TAIL
S = [0.0] + A[1:]
L = [S[k + 1] - S[k] + X for k in range(len(ids) - 1)] + [END - S[-1]]


def silences(wav):
    err = subprocess.run([FF, "-hide_banner", "-i", str(wav), "-af", "silencedetect=n=-38dB:d=0.18", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    en = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return [(a + b) / 2 for a, b in zip(st, en)]


def captions():
    cues = []
    for k, sc in enumerate(SCENES):
        sid = sc["id"]
        ch = caption_chunks(sc["say"])
        w = [len(c[0]) for c in ch]
        tot = sum(w)
        mids = silences(Path(f"../audio/{sid}.wav"))
        bounds, acc = [0.0], 0
        for x in w[:-1]:
            acc += x
            est = D[sid] * acc / tot
            near = min(mids, key=lambda m: abs(m - est)) if mids else est
            bounds.append(near if abs(near - est) < 0.9 and near > bounds[-1] + 0.6 else est)
        bounds.append(D[sid] + 0.15)
        for j, (_, disp) in enumerate(ch):
            cues.append(dict(sid=sid, chip=sc["chip"] if sid in APP else "", text=disp,
                             a=round(A[k] + bounds[j], 3), b=round(A[k] + bounds[j + 1], 3)))
    return cues


async def render_lowers(cues):
    b = await Browser(port=9350).start()
    await b.send("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
    for n, c in enumerate(cues):
        url = f"{LOWER}?chip={urllib.parse.quote(c['chip'])}&cap={urllib.parse.quote(c['text'])}"
        await b.goto(url, 0.35)
        await b.shot(TMP / f"low_{n:03d}.png")
    await b.close()


def srt(cues):
    f = lambda s: f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d},{int(round(s % 1 * 1000)) % 1000:03d}"
    return "\n".join(f"{n + 1}\n{f(c['a'])} --> {f(c['b'])}\n{c['text']}\n" for n, c in enumerate(cues))


def main():
    # 1) normalise clips to exact length L (first clip shifted by LEAD so its timeline matches narration)
    for k, sid in enumerate(ids):
        lead = LEAD if k == 0 else 0.0
        vf = (f"fps={FPS},scale=1920:1080:flags=lanczos:in_range=full:out_range=tv,setsar=1,format=yuv420p,"
              f"tpad=start_mode=clone:start_duration={lead}:stop_mode=clone:stop_duration=6,trim=duration={L[k]:.3f},setpts=PTS-STARTPTS")
        run(["-f", "concat", "-safe", "0", "-i", f"../clips/{sid}/list.txt", "-vf", vf, "-c:v", "libx264", "-preset", "medium",
             "-crf", "16", "-r", str(FPS), str(TMP / f"{sid}.mp4")])
    # 2) crossfade chain
    inputs, fc, prev, off = [], [], "0:v", 0.0
    for k, sid in enumerate(ids):
        inputs += ["-i", str(TMP / f"{sid}.mp4")]
    for k in range(1, len(ids)):
        off += L[k - 1] - X
        out = f"x{k}"
        fc.append(f"[{prev}][{k}:v]xfade=transition=fade:duration={X}:offset={off:.3f}[{out}]")
        prev = out
    fc.append(f"[{prev}]fade=t=in:st=0:d=0.6,fade=t=out:st={END - 0.8:.3f}:d=0.8[v]")
    run([*inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
         "-r", str(FPS), "-t", f"{END:.3f}", str(TMP / "video_nocap.mp4")])
    # 3) narration track
    ain, mix = [], []
    for k, sid in enumerate(ids):
        ain += ["-i", f"../audio/{sid}.wav"]
        ms = int(A[k] * 1000)
        mix.append(f"[{k}:a]adelay={ms}|{ms}[a{k}]")
    mix.append("".join(f"[a{k}]" for k in range(len(ids))) + f"amix=inputs={len(ids)}:normalize=0,apad,atrim=0:{END:.3f}[a]")
    run([*ain, "-filter_complex", ";".join(mix), "-map", "[a]", "-ar", "48000", "-ac", "2", str(TMP / "voice.wav")])
    # 4) captions
    cues = captions()
    json.dump(cues, open(TMP / "cues.json", "w"), indent=1)
    asyncio.run(render_lowers(cues))
    (OUT / "HealthPulse-Demo.srt").write_text(srt(cues))
    # one alpha overlay stream: blank / caption images timed with the concat demuxer
    from PIL import Image
    Image.new("RGBA", (1920, 1080), (0, 0, 0, 0)).save(TMP / "blank.png")
    lines, t = [], 0.0
    for n, c in enumerate(cues):
        if c["a"] > t + 0.001:
            lines += [f"file '{TMP / 'blank.png'}'", f"duration {c['a'] - t:.3f}"]
        lines += [f"file '{TMP / f'low_{n:03d}.png'}'", f"duration {c['b'] - c['a']:.3f}"]
        t = c["b"]
    lines += [f"file '{TMP / 'blank.png'}'", f"duration {max(END - t, 0.05):.3f}", f"file '{TMP / 'blank.png'}'"]
    (TMP / "lower.txt").write_text("\n".join(lines))
    ov_in = ["-i", str(TMP / "video_nocap.mp4"), "-i", str(TMP / "voice.wav"), "-f", "concat", "-safe", "0", "-i", str(TMP / "lower.txt")]
    ov = [f"[2:v]fps={FPS},format=rgba[lw]", "[0:v][lw]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[v]"]
    final = OUT / "HealthPulse-Demo.mp4"
    run([*ov_in, "-filter_complex", ";".join(ov), "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
         "-profile:v", "high", "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
         "-t", f"{END:.3f}", str(final)])
    # clean (no captions) version too
    run(["-i", str(TMP / "video_nocap.mp4"), "-i", str(TMP / "voice.wav"), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{END:.3f}", str(OUT / "HealthPulse-Demo-no-captions.mp4")])
    print(json.dumps(dict(end=END, scenes={i: (round(a, 2), round(l, 2)) for i, a, l in zip(ids, A, L)}, cues=len(cues))))


if __name__ == "__main__":
    main()
