#!/usr/bin/env python3
"""
vcam - pipeline de camera virtual para Android (LSPatch + modulo VCAM).

Converte um video para o formato exato que o modulo VCAM espera, gera um
frame de preview para conferir a orientacao antes de mexer no celular, e
envia tudo via adb.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

if getattr(sys, "frozen", False):
    # Empacotado: __file__ aponta para a pasta temporaria que o PyInstaller
    # apaga ao sair. Tudo tem que ficar ao lado do .exe, nao dentro dele.
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent

OUT = ROOT / "out"

# Como o usuario invoca a ferramenta, para as mensagens de ajuda saírem
# corretas tanto no script quanto no .exe.
INVOKE = "vcam.exe" if getattr(sys, "frozen", False) else "python vcam.py"
TOOLS = ROOT / "tools"
CONFIG = ROOT / "vcam.json"

VIDEO_NAME = "virtual.mp4"
FLAGS = ["no_toast.jpg", "force_show.jpg", "private_dir.jpg", "disable.jpg"]

# Cada modulo VCAM le de um lugar diferente. Por padrao escrevemos em todos
# os caminhos conhecidos: custa alguns MB e evita ter que adivinhar qual
# modulo esta instalado.
LAYOUTS = {
    "vcam": {
        "label": "VCAM-Revise / com.example.vcam / xCam",
        "dirs": [
            "/sdcard/DCIM/Camera1",
            "/sdcard/Android/data/{pkg}/files/Camera1",
        ],
        "stream": None,
    },
    "xvirtual": {
        "label": "XVirtualCamera",
        "dirs": ["/sdcard/Android/data/{pkg}/cache"],
        "stream": "/sdcard/Android/data/{pkg}/cache/stream.txt",
    },
}

PLATFORM_TOOLS_URL = (
    "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
)

__version__ = "1.0.0"

# Endereco do manifesto de atualizacao (JSON). Precisa ser https.
# Deixe vazio para desligar a atualizacao automatica.
#
# Exemplo de conteudo do manifesto:
#   {"version": "1.1.0",
#    "url": "https://.../vcam.exe",
#    "sha256": "abc123...",
#    "notes": "o que mudou"}
UPDATE_URL = (
    "https://github.com/amaralenja/vcam/releases/latest/download/update.json"
)

DEFAULTS = {
    "pkg": "com.discord",
    "res": "1280x720",
    "facing": "front",
    "fps": 30,
    "module": "auto",
}


def layout_keys(module):
    if not module or module == "auto":
        return list(LAYOUTS)
    if module not in LAYOUTS:
        raise Fail("modulo desconhecido: {} (use auto, {})".format(
            module, ", ".join(LAYOUTS)))
    return [module]


def resolve_dirs(pkg, module):
    """Pastas onde o virtual.mp4 deve ser escrito, sem repetir."""
    dirs = []
    for key in layout_keys(module):
        for template in LAYOUTS[key]["dirs"]:
            path = template.format(pkg=pkg)
            if path not in dirs:
                dirs.append(path)
    return dirs


def resolve_stream(pkg, module):
    """Caminho do stream.txt, se algum modulo escolhido suportar rede."""
    for key in layout_keys(module):
        template = LAYOUTS[key]["stream"]
        if template:
            return key, template.format(pkg=pkg)
    return None, None


# ---------------------------------------------------------------- utilidades

class Fail(Exception):
    """Erro esperado, mostrado sem stacktrace."""


def say(msg=""):
    print(msg, flush=True)


def ok(msg):
    say("  [ok] " + msg)


def warn(msg):
    say("  [!]  " + msg)


def bad(msg):
    say("  [x]  " + msg)


def run(cmd, check=True):
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise Fail("comando falhou: " + " ".join(cmd[:3]) + "\n" + detail[-1500:])
    return proc


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            warn("vcam.json ilegivel, usando padroes")
    return cfg


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ------------------------------------------------------------- localizadores

def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        raise Fail("ffmpeg nao encontrado no PATH.")
    return exe


def find_ffprobe():
    exe = shutil.which("ffprobe")
    if not exe:
        raise Fail("ffprobe nao encontrado (vem junto com o ffmpeg).")
    return exe


def find_adb(required=True):
    local = TOOLS / "platform-tools" / "adb.exe"
    if local.exists():
        return str(local)
    exe = shutil.which("adb")
    if exe:
        return exe
    if required:
        raise Fail("adb nao encontrado. Rode:  " + INVOKE + " setup-adb")
    return None


def adb(args, check=True, serial=None):
    cmd = [find_adb()]
    if serial:
        cmd += ["-s", serial]
    return run(cmd + args, check=check)


# O adb reporta varios estados alem de "device"; cada um pede uma acao
# diferente do usuario.
STATE_HELP = {
    "unauthorized": "autorize a depuracao na tela do celular (aparece um popup)",
    "offline": "reconecte: " + INVOKE + " connect IP:PORTA",
    "no": "sem permissao de acesso - problema de driver USB",
    "recovery": "aparelho esta em modo recovery",
    "sideload": "aparelho esta em modo sideload",
    "bootloader": "aparelho esta no bootloader, nao no Android",
}


def device_states():
    """Lista (serial, estado) de tudo que o adb enxerga."""
    exe = find_adb(required=False)
    if not exe:
        return []
    proc = run([exe, "devices"], check=False)
    out = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


def devices():
    return [serial for serial, state in device_states() if state == "device"]


def require_device(cfg):
    find_adb()  # levanta o erro certo se o adb nao estiver instalado
    states = device_states()
    ready = [serial for serial, state in states if state == "device"]

    if not ready:
        problems = [(s, st) for s, st in states if st != "device"]
        if problems:
            lines = ["o adb ve o aparelho, mas ele nao esta pronto:"]
            for serial, state in problems:
                lines.append("       {}  ->  {}".format(
                    serial, STATE_HELP.get(state, state)))
            raise Fail("\n".join(lines))
        raise Fail(
            "nenhum aparelho conectado.\n"
            "       USB: pluga o cabo e autoriza a depuracao na tela.\n"
            "       Wi-Fi: " + INVOKE + " connect IP:PORTA"
        )

    saved = cfg.get("serial")
    if saved in ready:
        return saved
    if len(ready) > 1:
        warn("varios aparelhos conectados, usando " + ready[0])
        say("      Os outros: " + ", ".join(ready[1:]))
    return ready[0]


def remote_size(path, serial):
    """Tamanho do arquivo no celular, ou None se nao der pra ler."""
    proc = adb(["shell", "stat", "-c", "%s", path], check=False, serial=serial)
    try:
        return int((proc.stdout or "").strip())
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ ffmpeg

def probe(path):
    try:
        return _probe(path)
    except Fail:
        raise Fail("nao consegui ler '{}' como video.\n"
                   "       O arquivo esta corrompido ou nao e um video.".format(path))


def _probe(path):
    proc = run([
        find_ffprobe(), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,duration:stream_tags=rotate",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise Fail("o arquivo nao tem faixa de video")
    stream = streams[0]
    fmt = data.get("format") or {}
    dur = stream.get("duration") or fmt.get("duration") or 0
    num, _, den = (stream.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = round(int(num) / int(den or 1), 2)
    except (ValueError, ZeroDivisionError):
        fps = 0
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": fps,
        "duration": round(float(dur), 2),
        "rotate": (stream.get("tags") or {}).get("rotate"),
    }


def orient_filters(facing, rotate, flip):
    """Rotacao e espelhamento, aplicados antes de qualquer enquadramento."""
    chain = []
    if rotate == 90:
        chain.append("transpose=1")
    elif rotate == 270:
        chain.append("transpose=2")
    elif rotate == 180:
        chain.append("transpose=1,transpose=1")

    # A camera frontal entrega imagem espelhada; o app espera isso.
    if flip is None:
        flip = facing == "front"
    if flip:
        chain.append("hflip")
    return chain


def build_filters(w, h, facing, rotate, flip,
                  fit="crop", zoom=1.0, pan_x=0.0, pan_y=0.0, blur=20):
    """Monta o filtergraph: orientacao -> enquadramento -> posicao.

    `pan_x`/`pan_y` vao de -1 (esquerda/topo) a 1 (direita/baixo), com 0 no
    centro. Sao convertidos para a fracao 0..1 que o ffmpeg usa nas
    expressoes de x/y.
    """
    chain = orient_filters(facing, rotate, flip)
    px = (pan_x + 1) / 2
    py = (pan_y + 1) / 2
    zw, zh = max(2, int(w * zoom)), max(2, int(h * zoom))

    if fit == "stretch":
        # Deforma para preencher. Rapido, mas achata ou estica o rosto.
        chain.append("scale={}:{}".format(w, h))

    elif fit == "contain":
        # Cabe inteiro, com tarja preta no que sobra.
        chain.append(
            "scale={}:{}:force_original_aspect_ratio=decrease".format(zw, zh))
        chain.append(
            "pad=ceil(max(iw\\,{})/2)*2:ceil(max(ih\\,{})/2)*2"
            ":(ow-iw)*{}:(oh-ih)*{}:black".format(w, h, px, py))
        chain.append("crop={}:{}:(iw-ow)*{}:(ih-oh)*{}".format(w, h, px, py))

    elif fit == "blur":
        # Video inteiro por cima de uma versao ampliada e borrada dele
        # mesmo: e como as redes sociais enchem quadro vertical com video
        # horizontal, sem tarja preta e sem cortar nada.
        prefix = ",".join(chain) + "," if chain else ""
        return (
            "{}split[bg][fg];"
            "[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            "crop={w}:{h},gblur=sigma={blur}[bgb];"
            "[fg]scale={zw}:{zh}:force_original_aspect_ratio=decrease[fgs];"
            "[bgb][fgs]overlay=(W-w)*{px}:(H-h)*{py},setsar=1"
        ).format(prefix, w=w, h=h, zw=zw, zh=zh, blur=blur, px=px, py=py)

    else:  # crop
        # Preenche o quadro e corta o excedente: sem tarja e sem distorcer.
        chain.append(
            "scale={}:{}:force_original_aspect_ratio=increase".format(zw, zh))
        chain.append(
            "crop={}:{}:(iw-ow)*{}:(ih-oh)*{}".format(w, h, px, py))

    chain.append("setsar=1")
    return ",".join(chain)


def encode(src, dst, vf, fps, seconds=None, start=None):
    cmd = [find_ffmpeg(), "-y"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += [
        "-vf", vf,
        "-r", str(fps),
        "-an",  # VCAM e so video; seu microfone real continua ao vivo
        "-c:v", "libx264",
        "-profile:v", "main",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "20",
        "-movflags", "+faststart",
        str(dst),
    ]
    run(cmd)


def seamless_loop(src, dst, fade):
    """Mistura o fim com o comeco para o loop nao ter corte visivel."""
    info = probe(src)
    dur = info["duration"]
    if dur <= fade * 2:
        raise Fail(
            "video curto demais ({}s) para um fade de {}s.".format(dur, fade)
        )
    # O corpo comeca em `fade`, perdendo os primeiros segundos - eles voltam
    # sobrepostos no final, com o alpha subindo de 0 a 1. Assim o ultimo
    # frame coincide com o primeiro e o loop nao tem salto.
    # Saida: dur - fade segundos.
    offset = dur - 2 * fade
    fc = (
        "[0]split[body][head];"
        "[head]trim=duration={fade},format=yuva420p,"
        "fade=t=in:d={fade}:alpha=1,setpts=PTS-STARTPTS+{offset}/TB[tail];"
        "[body]trim=start={fade},setpts=PTS-STARTPTS[main];"
        "[main][tail]overlay=eof_action=pass[v]"
    ).format(fade=fade, offset=offset)
    run([
        find_ffmpeg(), "-y", "-i", str(src),
        "-filter_complex", fc, "-map", "[v]",
        "-an", "-c:v", "libx264", "-profile:v", "main",
        "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
        "-movflags", "+faststart", str(dst),
    ])


def grab_frame(video, dst, at=1.0):
    run([
        find_ffmpeg(), "-y", "-ss", str(at), "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(dst),
    ])


def tiny_jpg(dst):
    run([
        find_ffmpeg(), "-y", "-f", "lavfi",
        "-i", "color=c=black:s=2x2", "-frames:v", "1", str(dst),
    ])


def parse_res(text):
    w_raw, sep, h_raw = text.lower().partition("x")
    if not sep:
        raise Fail("resolucao invalida: " + text + " (use por exemplo 1280x720)")
    try:
        w, h = int(w_raw), int(h_raw)
    except ValueError:
        raise Fail("resolucao invalida: " + text + " (use por exemplo 1280x720)")
    if w <= 0 or h <= 0:
        raise Fail("resolucao precisa ser positiva: " + text)
    if w > 7680 or h > 7680:
        raise Fail("resolucao absurda: " + text + " (maximo 7680)")
    if w % 2 or h % 2:
        raise Fail("largura e altura precisam ser pares (exigencia do H.264)")
    return w, h


def validate_build(args, fps):
    if fps <= 0 or fps > 240:
        raise Fail("fps invalido: {} (use algo entre 1 e 240)".format(fps))
    if args.seconds is not None and args.seconds <= 0:
        raise Fail("--seconds precisa ser maior que zero")
    if args.loop_fade is not None and args.loop_fade <= 0:
        raise Fail("--loop-fade precisa ser maior que zero")
    if not 0.1 <= args.zoom <= 5:
        raise Fail("--zoom precisa ficar entre 0.1 e 5")
    for name, value in (("--pan-x", args.pan_x), ("--pan-y", args.pan_y)):
        if not -1 <= value <= 1:
            raise Fail("{} precisa ficar entre -1 e 1 (0 = centro)".format(name))
    if not 0 <= args.blur <= 100:
        raise Fail("--blur precisa ficar entre 0 e 100")


# --------------------------------------------------------- servidor de video

def local_ip():
    """IP do PC na rede local, visto pelo celular."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # nao envia nada, so resolve a rota
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class _Slice:
    """Limita a leitura ao pedaco pedido no cabecalho Range."""

    def __init__(self, fh, remaining):
        self.fh = fh
        self.remaining = remaining

    def read(self, n=-1):
        if self.remaining <= 0:
            return b""
        if n is None or n < 0:
            n = self.remaining
        data = self.fh.read(min(n, self.remaining))
        self.remaining -= len(data)
        return data

    def close(self):
        self.fh.close()


class RangeHandler(SimpleHTTPRequestHandler):
    """Servidor de arquivos com suporte a Range.

    O player do Android pede pedacos do MP4 em vez do arquivo inteiro; sem
    Range ele trava ou nem comeca.
    """

    # So o video e servido. Sem listagem de pasta e sem expor o preview
    # nem qualquer outra coisa que caia em out/.
    ALLOWED = ("/" + VIDEO_NAME,)

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(OUT), **kw)

    def log_message(self, fmt, *args):
        pass

    def _allowed(self):
        return self.path.split("?", 1)[0] in self.ALLOWED

    def do_GET(self):
        if not self._allowed():
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        if not self._allowed():
            self.send_error(404)
            return
        super().do_HEAD()

    def send_head(self):
        header = self.headers.get("Range")
        if not header:
            return super().send_head()

        path = self.translate_path(self.path)
        try:
            fh = open(path, "rb")
        except OSError:
            self.send_error(404)
            return None

        size = os.fstat(fh.fileno()).st_size
        unit, _, spec = header.partition("=")
        start_raw, _, end_raw = spec.partition("-")
        try:
            start = int(start_raw) if start_raw else 0
            end = int(end_raw) if end_raw else size - 1
        except ValueError:
            fh.close()
            self.send_error(400, "Range invalido")
            return None

        if unit.strip().lower() != "bytes" or start >= size or start < 0:
            fh.close()
            self.send_error(416)
            return None

        end = min(end, size - 1)
        length = end - start + 1
        fh.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Range",
                         "bytes {}-{}/{}".format(start, end, size))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return _Slice(fh, length)


# ------------------------------------------------------------- atualizacao

def parse_version(text):
    """'1.2.10' -> (1, 2, 10). Partes nao numericas viram 0."""
    parts = []
    for chunk in str(text).strip().lstrip("vV").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


def update_url(cfg):
    """URL do manifesto: config sobrepoe o valor embutido no programa."""
    return (cfg.get("update_url") or UPDATE_URL or "").strip()


def fetch_manifest(url):
    if not url:
        raise Fail(
            "atualizacao automatica nao configurada.\n"
            "       Defina UPDATE_URL no vcam.py antes de gerar o .exe, ou\n"
            "       ponha \"update_url\" no vcam.json ao lado do executavel.")
    if not url.lower().startswith("https://"):
        # Sem https qualquer um na rede poderia trocar o binario baixado.
        raise Fail("a URL de atualizacao precisa ser https: " + url)
    try:
        with urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, OSError) as exc:
        raise Fail("nao consegui buscar o manifesto: {}".format(exc))
    except json.JSONDecodeError as exc:
        raise Fail("o manifesto nao e um JSON valido: {}".format(exc))

    for field in ("version", "url", "sha256"):
        if not data.get(field):
            raise Fail("manifesto incompleto: falta '{}'".format(field))
    if not str(data["url"]).lower().startswith("https://"):
        raise Fail("a URL do executavel precisa ser https")
    return data


def download_verified(url, sha256, dest, progress=None):
    """Baixa e so aceita se o SHA-256 bater. Sem isso, seria executar
    qualquer coisa que voltasse da rede."""
    import hashlib

    digest = hashlib.sha256()
    try:
        with urlopen(url, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except (URLError, HTTPError, OSError) as exc:
        dest.unlink(missing_ok=True)
        raise Fail("falhou o download: {}".format(exc))

    got = digest.hexdigest()
    if got.lower() != str(sha256).lower().strip():
        dest.unlink(missing_ok=True)
        raise Fail("o arquivo baixado nao confere com o esperado.\n"
                   "       esperado: {}\n"
                   "       recebido: {}\n"
                   "       Atualizacao cancelada por seguranca.".format(
                       sha256, got))
    return dest


def swap_and_restart(new_exe, current_exe):
    """Troca o executavel e reabre.

    O Windows nao deixa um .exe em uso ser sobrescrito, entao um .bat
    espera este processo morrer, troca os arquivos e reabre.
    """
    script = ROOT / "_vcam_update.bat"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        ":wait",
        'tasklist /FI "PID eq {}" 2>nul | find "{}" >nul'.format(
            os.getpid(), os.getpid()),
        "if not errorlevel 1 (",
        "  ping -n 2 127.0.0.1 >nul",
        "  goto wait",
        ")",
        'move /y "{}" "{}" >nul'.format(new_exe, current_exe),
        'if errorlevel 1 ( echo Falhou a troca do executavel. & pause & exit /b 1 )',
        'start "" "{}"'.format(current_exe),
        'del "%~f0"',
    ]
    script.write_text("\r\n".join(lines), encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(script)],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return script


def do_update(cfg, check_only=False, progress=None, log=say):
    """Retorna (tem_novidade, manifesto). Se aplicar, encerra o programa."""
    manifest = fetch_manifest(update_url(cfg))
    remote = parse_version(manifest["version"])
    local = parse_version(__version__)

    if remote <= local:
        log("Voce ja esta na versao mais recente ({}).".format(__version__))
        return False, manifest

    log("Versao nova disponivel: {}  (voce tem {})".format(
        manifest["version"], __version__))
    if manifest.get("notes"):
        log("Mudancas: " + str(manifest["notes"]))
    if check_only:
        return True, manifest

    if not getattr(sys, "frozen", False):
        raise Fail("a atualizacao automatica so funciona no .exe.\n"
                   "       Rodando pelo codigo-fonte, use o git ou baixe "
                   "os arquivos novos.")

    current = Path(sys.executable).resolve()
    staged = current.with_name(current.stem + "-novo.exe")
    log("Baixando...")
    download_verified(manifest["url"], manifest["sha256"], staged,
                      progress=progress)
    log("Verificado. Trocando o executavel...")
    swap_and_restart(staged, current)
    return True, manifest


# ----------------------------------------------------------------- comandos

def cmd_version(args):
    say("vcam " + __version__)
    cfg = load_config()
    url = update_url(cfg)
    say("atualizacao: " + (url if url else "nao configurada"))
    return 0


def cmd_update(args):
    cfg = load_config()
    changed, _ = do_update(cfg, check_only=args.check)
    if changed and not args.check:
        say()
        ok("o programa vai fechar e reabrir atualizado")
        # Sai imediatamente: o .bat esta esperando este processo morrer.
        sys.exit(0)
    return 0


def cmd_serve(args):
    cfg = load_config()
    pkg = args.pkg or cfg["pkg"]
    video = OUT / VIDEO_NAME
    if not video.exists():
        raise Fail("nenhum video convertido. Roda antes:  "
                   "" + INVOKE + " build SEU.mp4")

    module = args.module or cfg.get("module", "auto")
    key, remote = resolve_stream(pkg, module)
    if not remote:
        raise Fail(
            "o modulo '{}' nao le fonte em rede.\n"
            "       Streaming exige o XVirtualCamera. Com os outros, use:  "
            "" + INVOKE + " push".format(module))

    if module == "auto":
        warn("modulo em auto - assumindo " + LAYOUTS[key]["label"])
        say("      Se voce usa outro modulo, o streaming nao vai funcionar.")
        say("      Confirma com:  " + INVOKE + " detect")
        say()

    port = args.port
    serial = None if args.no_push else require_device(cfg)
    tunnel = False

    # adb reverse faz o celular chamar o proprio 127.0.0.1, e o adb
    # encaminha ate o PC. Assim nao depende do IP da rede nem do Firewall
    # do Windows - e com cabo USB nem depende do Wi-Fi.
    if serial and not args.lan:
        spec = "tcp:{}".format(port)
        proc = adb(["reverse", spec, spec], check=False, serial=serial)
        if proc.returncode == 0:
            tunnel = True
            ok("tunel adb reverse ativo (dispensa IP e Firewall)")
        else:
            warn("adb reverse falhou, usando o IP da rede local")

    if tunnel:
        host = "127.0.0.1"
    else:
        host = args.host or local_ip()
    url = "http://{}:{}/{}".format(host, port, VIDEO_NAME)

    if serial:
        pointer = OUT / "stream.txt"
        pointer.write_text(url, encoding="utf-8")  # sem linha em branco extra
        try:
            adb(["shell", "mkdir", "-p", remote.rsplit("/", 1)[0]],
                check=False, serial=serial)
            proc = adb(["push", str(pointer), remote],
                       check=False, serial=serial)
        finally:
            pointer.unlink(missing_ok=True)
        if proc.returncode == 0:
            ok("apontei o modulo para " + url)
        else:
            bad("nao consegui escrever o stream.txt:")
            say("      " + (proc.stderr or proc.stdout).strip()[:200])
            say("      Escreve na mao em " + remote)
            say("      com esta unica linha (sem linha em branco):")
            say("      " + url)

    if not tunnel:
        say()
        warn("sem tunel: o celular precisa estar no MESMO Wi-Fi que o PC,")
        say("      e o Firewall do Windows tem que liberar a porta "
            + str(port) + ".")

    cfg["pkg"] = pkg
    save_config(cfg)

    bind = "127.0.0.1" if tunnel else "0.0.0.0"
    try:
        server = ThreadingHTTPServer((bind, port), RangeHandler)
    except OSError as exc:
        raise Fail("nao consegui abrir a porta {}: {}".format(port, exc))

    say()
    ok("servindo " + str(OUT))
    ok("url: " + url)
    say()
    say("Deixa esta janela aberta durante a call.")
    say("Pra trocar o video, roda o build em outro terminal - o celular")
    say("pega a versao nova na proxima vez que a camera abrir.")
    say()
    say("Ctrl+C para parar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say()
        ok("servidor parado")
    finally:
        server.server_close()
        if tunnel:
            adb(["reverse", "--remove", "tcp:{}".format(port)],
                check=False, serial=serial)
            ok("tunel removido")
    return 0


def cmd_unstream(args):
    cfg = load_config()
    pkg = args.pkg or cfg["pkg"]
    serial = require_device(cfg)
    removed = False
    for key in LAYOUTS:
        template = LAYOUTS[key]["stream"]
        if not template:
            continue
        remote = template.format(pkg=pkg)
        adb(["shell", "rm", "-f", remote], check=False, serial=serial)
        ok("removido: " + remote)
        removed = True
    if not removed:
        warn("nenhum layout com streaming conhecido")
    say()
    say("O modulo volta a usar o arquivo local do celular.")
    return 0


def cmd_doctor(args):
    cfg = load_config()
    say("Diagnostico")
    say()

    for name, finder in (("ffmpeg", find_ffmpeg), ("ffprobe", find_ffprobe)):
        try:
            ok(name + ": " + finder())
        except Fail:
            bad(name + ": nao encontrado - instale o ffmpeg e coloque no PATH")

    exe = find_adb(required=False)
    if exe:
        ok("adb: " + exe)
        found = devices()
        if found:
            for serial in found:
                ok("aparelho: " + serial)
        else:
            warn("nenhum aparelho conectado")
            say("       Liga a Depuracao sem fio no celular e roda:")
            say("       " + INVOKE + " connect IP:PORTA")
    else:
        bad("adb: nao encontrado")
        say("       Roda:  " + INVOKE + " setup-adb")

    say()
    say("Configuracao atual")
    ok("app alvo:   " + cfg["pkg"])
    ok("resolucao:  " + cfg["res"])
    ok("camera:     " + cfg["facing"])
    ok("fps:        " + str(cfg["fps"]))
    module = cfg.get("module", "auto")
    if module == "auto":
        ok("modulo:     auto (escreve em todos os caminhos)")
    else:
        ok("modulo:     " + LAYOUTS[module]["label"])
    if (OUT / VIDEO_NAME).exists():
        info = probe(OUT / VIDEO_NAME)
        ok("video pronto: {}x{} {}fps {}s".format(
            info["width"], info["height"], info["fps"], info["duration"]))
    else:
        warn("nenhum video convertido ainda")
    return 0


def cmd_setup_adb(args):
    dest = TOOLS / "platform-tools"
    if dest.exists() and not args.force:
        ok("adb ja instalado em " + str(dest))
        return 0
    TOOLS.mkdir(parents=True, exist_ok=True)
    zip_path = TOOLS / "platform-tools.zip"
    say("Baixando platform-tools do servidor oficial do Google...")
    say("  " + PLATFORM_TOOLS_URL)
    try:
        with urlopen(PLATFORM_TOOLS_URL, timeout=180) as resp:
            with open(zip_path, "wb") as fh:
                shutil.copyfileobj(resp, fh)
    except (URLError, HTTPError, OSError) as exc:
        zip_path.unlink(missing_ok=True)
        raise Fail("falhou o download: {}\n"
                   "       Confere a internet, ou baixa na mao em\n"
                   "       https://developer.android.com/tools/releases/"
                   "platform-tools\n"
                   "       e extrai em {}".format(exc, TOOLS))

    say("Extraindo ({} MB)...".format(max(1, zip_path.stat().st_size // 1048576)))
    try:
        if dest.exists():
            shutil.rmtree(dest)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(TOOLS)
    except (zipfile.BadZipFile, OSError) as exc:
        raise Fail("o arquivo baixado veio corrompido: {}\n"
                   "       Roda de novo: " + INVOKE + " setup-adb --force".format(exc))
    finally:
        zip_path.unlink(missing_ok=True)

    exe = dest / "adb.exe"
    if not exe.exists():
        raise Fail("extraiu, mas nao achei o adb.exe em " + str(dest))
    ok("adb instalado: " + str(exe))
    return 0


def cmd_connect(args):
    exe = find_adb()
    if args.pair:
        say("Pareando com " + args.pair + " ...")
        say("Digite o codigo de 6 digitos que aparece no celular:")
        code = input("  codigo: ").strip()
        proc = run([exe, "pair", args.pair, code], check=False)
        say((proc.stdout or proc.stderr).strip())
    if args.address:
        proc = run([exe, "connect", args.address], check=False)
        say((proc.stdout or proc.stderr).strip())
    found = devices()
    if not found:
        raise Fail("nao conectou. Confere se a Depuracao sem fio esta ligada "
                   "e se o PC esta na mesma rede Wi-Fi.")
    for serial in found:
        ok("conectado: " + serial)
    cfg = load_config()
    cfg["serial"] = found[0]
    save_config(cfg)
    return 0


def cmd_probe(args):
    info = probe(args.video)
    say(args.video)
    say("  resolucao: {}x{}".format(info["width"], info["height"]))
    say("  fps:       {}".format(info["fps"]))
    say("  duracao:   {}s".format(info["duration"]))
    if info["rotate"]:
        say("  rotacao:   {} (metadado)".format(info["rotate"]))
    return 0


def cmd_build(args):
    cfg = load_config()
    # Checagem explicita contra None: com "or", um --fps 0 virava o padrao
    # em silencio em vez de ser recusado.
    res = args.res if args.res is not None else cfg["res"]
    facing = args.facing if args.facing is not None else cfg["facing"]
    fps = args.fps if args.fps is not None else cfg["fps"]
    w, h = parse_res(res)
    validate_build(args, fps)

    src = Path(args.video)
    if not src.exists():
        raise Fail("arquivo nao encontrado: " + str(src))
    if src.is_dir():
        raise Fail("isso e uma pasta, nao um video: " + str(src))
    if src.stat().st_size == 0:
        raise Fail("arquivo vazio: " + str(src))

    OUT.mkdir(parents=True, exist_ok=True)
    final = OUT / VIDEO_NAME
    info = probe(src)

    say("Entrada:  {}x{} {}fps {}s".format(
        info["width"], info["height"], info["fps"], info["duration"]))
    say("Saida:    {}x{} {}fps  camera {}".format(w, h, fps, facing))

    # Avisa quando o recorte vai comer muito da imagem - e a causa mais
    # comum de "cortou minha cabeca". So vale no modo crop; contain e blur
    # nao cortam nada.
    src_w, src_h = info["width"], info["height"]
    if args.fit == "crop" and src_w and src_h:
        if args.rotate in (90, 270):
            src_w, src_h = src_h, src_w
        src_ar, dst_ar = src_w / src_h, w / h
        keep = min(src_ar, dst_ar) / max(src_ar, dst_ar)
        if keep < 0.75:
            warn("as proporcoes sao bem diferentes: "
                 "~{}% da imagem vai ser cortada.".format(round((1 - keep) * 100)))
            say("      Pra nao perder nada, tenta:  --fit blur")
            say()

    vf = build_filters(w, h, facing, args.rotate, args.flip,
                       fit=args.fit, zoom=args.zoom,
                       pan_x=args.pan_x, pan_y=args.pan_y, blur=args.blur)
    say("Enquadramento: {}  zoom {}  pan {},{}".format(
        args.fit, args.zoom, args.pan_x, args.pan_y))
    say()

    preview = OUT / "preview.jpg"
    if args.preview_only:
        # So um frame com os filtros aplicados: iteracao instantanea
        # enquanto voce acerta o enquadramento.
        at = args.start or min(1.0, info["duration"] / 2)
        run([find_ffmpeg(), "-y", "-ss", str(at), "-i", str(src),
             "-vf", vf, "-frames:v", "1", "-q:v", "2", str(preview)])
        ok("preview: " + str(preview))
        say()
        say("Ajusta com --pan-x/--pan-y (-1 a 1), --zoom e --fit.")
        say("Quando gostar, roda de novo sem --preview-only.")
        return 0

    stage = OUT / "_stage.mp4" if args.loop_fade else final
    try:
        encode(src, stage, vf, fps, seconds=args.seconds, start=args.start)
        if args.loop_fade:
            say("Costurando o loop ({}s de crossfade)...".format(args.loop_fade))
            seamless_loop(stage, final, args.loop_fade)
    finally:
        if args.loop_fade:
            stage.unlink(missing_ok=True)

    if not final.exists() or final.stat().st_size == 0:
        raise Fail("o ffmpeg terminou sem erro mas nao gerou video.\n"
                   "       Tenta com outro arquivo de entrada.")

    out_info = probe(final)
    if (out_info["width"], out_info["height"]) != (w, h):
        warn("a saida ficou {}x{} em vez de {}x{}".format(
            out_info["width"], out_info["height"], w, h))

    grab_frame(final, preview, at=min(1.0, out_info["duration"] / 2))

    cfg.update({"res": res, "facing": facing, "fps": fps})
    save_config(cfg)

    say()
    ok("video:   " + str(final))
    ok("         {}x{} {}fps {}s".format(
        out_info["width"], out_info["height"],
        out_info["fps"], out_info["duration"]))
    ok("preview: " + str(preview))
    say()
    say("Abre o preview e confere ANTES de mandar pro celular:")
    say("  - o rosto esta na vertical?")
    say("  - o espelhamento parece natural (como selfie)?")
    say("  - nao cortou a cabeca?")
    say()
    say("Torto?  ajusta com --rotate 90|180|270 e/ou --flip / --no-flip")
    say("Certo?  " + INVOKE + " push")
    return 0


def cmd_push(args):
    cfg = load_config()
    pkg = args.pkg or cfg["pkg"]
    module = args.module or cfg.get("module", "auto")
    video = OUT / VIDEO_NAME
    if not video.exists():
        raise Fail("nenhum video convertido. Roda antes:  "
                   "" + INVOKE + " build SEU.mp4")

    dirs = resolve_dirs(pkg, module)
    serial = require_device(cfg)
    if module == "auto":
        say("Modulo: auto - escrevendo em todos os caminhos conhecidos.")
    else:
        say("Modulo: " + LAYOUTS[module]["label"])
    say()

    local_size = video.stat().st_size
    sent, blocked = [], []
    for folder in dirs:
        remote = folder + "/" + VIDEO_NAME
        adb(["shell", "mkdir", "-p", folder], check=False, serial=serial)
        proc = adb(["push", str(video), remote], check=False, serial=serial)
        if proc.returncode != 0:
            warn(folder + "   (inacessivel)")
            blocked.append(folder)
            continue
        # O adb as vezes devolve 0 mesmo com escrita parcial: confere o tamanho.
        size = remote_size(remote, serial)
        if size is None:
            ok(folder + "   (enviado, tamanho nao verificavel)")
            sent.append(folder)
        elif size != local_size:
            bad("{}   (chegou {} de {} bytes)".format(folder, size, local_size))
            blocked.append(folder)
        else:
            ok(folder)
            sent.append(folder)

    if not sent:
        raise Fail("nenhum caminho aceitou o arquivo. O aparelho pode estar "
                   "bloqueando Android/data pelo adb.")

    if blocked:
        say()
        say("Os caminhos marcados com [!] nao existem ou estao bloqueados.")
        say("Isso e normal: so o modulo que voce instalou usa o seu.")

    cfg["pkg"] = pkg
    cfg["module"] = module
    save_config(cfg)
    say()
    say("Abre o " + pkg + " modificado e entra numa call de video sozinho.")
    say("Anota a resolucao do toast. Se for diferente da atual, roda:")
    say("  " + INVOKE + " build SEU.mp4 --res LARGURAxALTURA")
    return 0


def cmd_detect(args):
    cfg = load_config()
    pkg = args.pkg or cfg["pkg"]
    serial = require_device(cfg)

    say("Procurando pastas de modulo VCAM em " + pkg)
    say()
    say("Dica: abre a camera no app modificado uma vez antes de rodar isto -")
    say("o modulo so cria a pasta dele no primeiro uso.")
    say()

    hits = []
    for key, layout in LAYOUTS.items():
        found = []
        for template in layout["dirs"]:
            folder = template.format(pkg=pkg)
            proc = adb(["shell", "ls", "-d", folder], check=False, serial=serial)
            exists = proc.returncode == 0 and "No such" not in proc.stdout
            mark = "[ok]" if exists else "[--]"
            say("  {} {}".format(mark, folder))
            if exists:
                found.append(folder)
        if found:
            hits.append(key)

    say()
    if len(hits) == 1:
        key = hits[0]
        ok("provavel modulo: " + LAYOUTS[key]["label"])
        if LAYOUTS[key]["stream"]:
            ok("suporta streaming (serve)")
        else:
            warn("nao suporta streaming - use push")
        if args.save:
            cfg["module"] = key
            cfg["pkg"] = pkg
            save_config(cfg)
            ok("salvo em vcam.json")
        else:
            say()
            say("Pra fixar isso:  " + INVOKE + " detect --save")
    elif len(hits) > 1:
        warn("mais de um layout encontrado: " + ", ".join(hits))
        say("       Deixa em auto, que escreve nos dois.")
    else:
        warn("nenhuma pasta de modulo encontrada.")
        say("       Abre a camera no app modificado uma vez e tenta de novo.")
        say("       Ate la, o modo auto cobre todos os caminhos.")
    return 0


def cmd_flags(args):
    cfg = load_config()
    pkg = args.pkg or cfg["pkg"]
    module = args.module or cfg.get("module", "auto")
    folders = resolve_dirs(pkg, module)

    wanted = set()
    if args.no_toast:
        wanted.add("no_toast.jpg")
    if args.private_dir:
        wanted.add("private_dir.jpg")
    if args.disable:
        wanted.add("disable.jpg")
    if args.force_show:
        wanted.add("force_show.jpg")

    if not wanted and not args.clear:
        raise Fail("escolha ao menos uma flag (--no-toast, --private-dir, "
                   "--disable, --force-show) ou use --clear")

    serial = require_device(cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    blank = OUT / "_flag.jpg"
    if wanted:
        tiny_jpg(blank)

    try:
        for folder in folders:
            adb(["shell", "mkdir", "-p", folder], check=False, serial=serial)
            for name in FLAGS:
                remote = folder + "/" + name
                if name in wanted:
                    proc = adb(["push", str(blank), remote],
                               check=False, serial=serial)
                    if proc.returncode == 0:
                        ok(name + "  ->  " + folder)
                elif args.clear:
                    adb(["shell", "rm", "-f", remote],
                        check=False, serial=serial)
    finally:
        blank.unlink(missing_ok=True)

    if args.clear and not wanted:
        ok("todas as flags removidas")
    cfg["module"] = module
    save_config(cfg)
    return 0


def cmd_go(args):
    rc = cmd_build(args)
    if rc:
        return rc
    if args.preview_only:
        # Nao ha video novo para enviar; enviar o antigo seria enganoso.
        say("(--preview-only: nada foi enviado)")
        return 0
    say()
    say("-" * 58)
    say()
    return cmd_push(args)


def cmd_clean(args):
    cfg = load_config()
    pkg = args.pkg or cfg["pkg"]
    serial = require_device(cfg)

    # Por padrao apaga so os arquivos que a gente escreveu. As pastas podem
    # ter conteudo do app (cache/) ou do usuario (DCIM/), entao remover a
    # pasta inteira so acontece com --purge.
    ours = [VIDEO_NAME, "stream.txt"] + FLAGS
    for layout in LAYOUTS.values():
        for template in layout["dirs"]:
            folder = template.format(pkg=pkg)
            if args.purge and not folder.endswith("/cache"):
                adb(["shell", "rm", "-rf", folder], check=False, serial=serial)
                ok("pasta removida: " + folder)
            else:
                for name in ours:
                    adb(["shell", "rm", "-f", folder + "/" + name],
                        check=False, serial=serial)
                ok("arquivos vcam limpos em " + folder)

    say()
    say("A camera real volta ao normal no proximo uso do app.")
    if not args.purge:
        say("Pra remover tambem as pastas vazias:  " + INVOKE + " clean --purge")
    return 0


# -------------------------------------------------------------------- main

def add_build_args(sp):
    sp.add_argument("video", help="seu MP4 de origem")
    sp.add_argument("--res", help="resolucao alvo, ex: 1280x720")
    sp.add_argument("--facing", choices=["front", "back"],
                    help="camera alvo (front espelha por padrao)")
    sp.add_argument("--fps", type=int, help="frames por segundo (padrao 30)")
    sp.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0,
                    help="girar a imagem em graus")
    sp.add_argument("--flip", dest="flip", action="store_true", default=None,
                    help="forcar espelhamento horizontal")
    sp.add_argument("--no-flip", dest="flip", action="store_false",
                    help="desligar o espelhamento")
    sp.add_argument("--seconds", type=float, help="cortar para N segundos")
    sp.add_argument("--start", help="comecar em HH:MM:SS")
    sp.add_argument("--loop-fade", type=float, metavar="N",
                    help="crossfade de N segundos para o loop nao ter corte")

    sp.add_argument("--fit", choices=["crop", "blur", "contain", "stretch"],
                    default="crop",
                    help="horizontal em quadro vertical: crop corta, "
                         "blur enche com fundo borrado, contain poe tarja "
                         "preta, stretch deforma")
    sp.add_argument("--zoom", type=float, default=1.0,
                    help="aproximar (>1) ou afastar (<1). Padrao 1.0")
    sp.add_argument("--pan-x", type=float, default=0.0, metavar="N",
                    help="mover na horizontal: -1 esquerda, 0 centro, 1 direita")
    sp.add_argument("--pan-y", type=float, default=0.0, metavar="N",
                    help="mover na vertical: -1 topo, 0 centro, 1 baixo")
    sp.add_argument("--blur", type=float, default=20, metavar="N",
                    help="intensidade do fundo borrado no --fit blur")
    sp.add_argument("--preview-only", action="store_true",
                    help="gera so o preview, sem codificar o video (rapido)")


def main():
    p = argparse.ArgumentParser(
        prog="vcam",
        description="Pipeline de camera virtual para Android (LSPatch + VCAM).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "doctor", help="checa ffmpeg, adb, aparelho e config"
    ).set_defaults(func=cmd_doctor)

    sub.add_parser("version", help="mostra a versao").set_defaults(
        func=cmd_version)

    sp = sub.add_parser("update", help="baixa e instala a versao mais nova")
    sp.add_argument("--check", action="store_true",
                    help="so avisa se tem novidade, sem baixar")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("setup-adb", help="baixa o adb oficial do Google")
    sp.add_argument("--force", action="store_true", help="reinstala mesmo se existir")
    sp.set_defaults(func=cmd_setup_adb)

    sp = sub.add_parser("connect", help="conecta no celular por Wi-Fi")
    sp.add_argument("address", nargs="?", help="IP:PORTA da Depuracao sem fio")
    sp.add_argument("--pair", metavar="IP:PORTA", help="IP:PORTA de pareamento")
    sp.set_defaults(func=cmd_connect)

    sp = sub.add_parser("probe", help="mostra info de um video")
    sp.add_argument("video")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("build", help="converte o video para o formato do VCAM")
    add_build_args(sp)
    sp.set_defaults(func=cmd_build)

    module_choices = ["auto"] + list(LAYOUTS)
    module_help = "layout do modulo: " + ", ".join(module_choices)

    sp = sub.add_parser("push", help="envia o video convertido para o celular")
    sp.add_argument("--pkg", help="pacote do app alvo (padrao com.discord)")
    sp.add_argument("--module", choices=module_choices, help=module_help)
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("go", help="build + push de uma vez")
    add_build_args(sp)
    sp.add_argument("--pkg", help="pacote do app alvo")
    sp.add_argument("--module", choices=module_choices, help=module_help)
    sp.set_defaults(func=cmd_go)

    sp = sub.add_parser("detect", help="descobre qual modulo VCAM esta instalado")
    sp.add_argument("--pkg")
    sp.add_argument("--save", action="store_true",
                    help="grava o resultado em vcam.json")
    sp.set_defaults(func=cmd_detect)

    sp = sub.add_parser("flags", help="liga/desliga os arquivos de controle do VCAM")
    sp.add_argument("--pkg")
    sp.add_argument("--module", choices=module_choices, help=module_help)
    sp.add_argument("--no-toast", action="store_true",
                    help="esconde o aviso de resolucao (use antes da call real)")
    sp.add_argument("--private-dir", action="store_true",
                    help="forca o modulo a usar a pasta privada do app")
    sp.add_argument("--disable", action="store_true", help="desliga o modulo")
    sp.add_argument("--force-show", action="store_true", help="reexibe o aviso")
    sp.add_argument("--clear", action="store_true", help="remove as flags")
    sp.set_defaults(func=cmd_flags)

    sp = sub.add_parser("serve", help="roda o video do PC e aponta o celular pra ele")
    sp.add_argument("--pkg", help="pacote do app alvo")
    sp.add_argument("--module", choices=module_choices, help=module_help)
    sp.add_argument("--port", type=int, default=8000, help="porta HTTP (padrao 8000)")
    sp.add_argument("--host", help="forcar um IP em vez de detectar automaticamente")
    sp.add_argument("--lan", action="store_true",
                    help="usar o IP da rede em vez do tunel adb reverse")
    sp.add_argument("--no-push", action="store_true",
                    help="so sobe o servidor, sem tunel nem stream.txt")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("unstream", help="volta a usar o arquivo local do celular")
    sp.add_argument("--pkg")
    sp.set_defaults(func=cmd_unstream)

    sp = sub.add_parser("clean", help="apaga os arquivos do celular")
    sp.add_argument("--pkg")
    sp.add_argument("--purge", action="store_true",
                    help="remove tambem as pastas, nao so os arquivos")
    sp.set_defaults(func=cmd_clean)

    args = p.parse_args()
    try:
        return args.func(args) or 0
    except Fail as exc:
        say()
        bad(str(exc))
        return 1
    except KeyboardInterrupt:
        say()
        return 130
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Rede caiu, disco cheio, permissao negada: mostra o motivo em vez
        # de despejar um stacktrace na cara do usuario.
        say()
        bad("{}: {}".format(type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
