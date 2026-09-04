#!/usr/bin/env python3
"""
Janela do vcam - a mesma coisa que o vcam.py faz, so que clicando.

Roda direto (python vcam_gui.py) ou empacotado como .exe. Se receber
argumentos de linha de comando, cai no modo CLI do vcam.py.
"""

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import vcam

PAD = 8


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("vcam {} - camera virtual".format(vcam.__version__))
        self.root.minsize(880, 620)

        self.cfg = vcam.load_config()
        self.busy = False
        self.log_queue = queue.Queue()
        self.preview_image = None

        self._build_widgets()
        self._pump_log()
        self.check_tools()
        threading.Thread(target=self._silent_update_check, daemon=True).start()

    def _silent_update_check(self):
        """Avisa se ha versao nova, sem incomodar nem travar a abertura.

        Falha em silencio: sem internet, servidor fora do ar ou atualizacao
        nao configurada nao sao problema do usuario neste momento.
        """
        try:
            cfg = vcam.load_config()
            if not vcam.update_url(cfg):
                return
            found, manifest = vcam.do_update(cfg, check_only=True,
                                             log=lambda _="": None)
            if found:
                self.say("[!] Versao {} disponivel (voce tem {}).".format(
                    manifest["version"], vcam.__version__))
                self.say("    Clique em \"Procurar atualizacao\".")
        except Exception:
            pass

    # ------------------------------------------------------------- widgets

    def _build_widgets(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        top = ttk.Frame(root, padding=PAD)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Video:").grid(row=0, column=0, sticky="w")
        self.video_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.video_var).grid(
            row=0, column=1, sticky="ew", padx=(PAD, PAD))
        ttk.Button(top, text="Procurar...", command=self.pick_video).grid(
            row=0, column=2)

        left = ttk.Frame(root, padding=(PAD, 0, PAD, PAD))
        left.grid(row=1, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)

        # --- camera
        cam = ttk.LabelFrame(left, text="Camera", padding=PAD)
        cam.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        cam.columnconfigure(1, weight=1)

        ttk.Label(cam, text="Resolucao").grid(row=0, column=0, sticky="w")
        self.res_var = tk.StringVar(value=self.cfg["res"])
        ttk.Entry(cam, textvariable=self.res_var, width=12).grid(
            row=0, column=1, sticky="w", padx=PAD)
        ttk.Label(cam, text="(a que o app mostrar no toast)",
                  foreground="#666").grid(row=0, column=2, sticky="w")

        ttk.Label(cam, text="Lente").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.facing_var = tk.StringVar(value=self.cfg["facing"])
        lens = ttk.Frame(cam)
        lens.grid(row=1, column=1, columnspan=2, sticky="w", padx=PAD, pady=(6, 0))
        ttk.Radiobutton(lens, text="Frontal", value="front",
                        variable=self.facing_var,
                        command=self.on_facing).pack(side="left")
        ttk.Radiobutton(lens, text="Traseira", value="back",
                        variable=self.facing_var,
                        command=self.on_facing).pack(side="left", padx=(PAD, 0))

        ttk.Label(cam, text="Girar").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.rotate_var = tk.StringVar(value="0")
        ttk.Combobox(cam, textvariable=self.rotate_var, width=6, state="readonly",
                     values=["0", "90", "180", "270"]).grid(
            row=2, column=1, sticky="w", padx=PAD, pady=(6, 0))

        self.flip_var = tk.BooleanVar(value=self.cfg["facing"] == "front")
        ttk.Checkbutton(cam, text="Espelhar (natural na frontal)",
                        variable=self.flip_var).grid(
            row=2, column=2, sticky="w", pady=(6, 0))

        # --- enquadramento
        fit = ttk.LabelFrame(left, text="Enquadramento", padding=PAD)
        fit.grid(row=1, column=0, sticky="ew", pady=(0, PAD))
        fit.columnconfigure(1, weight=1)

        ttk.Label(fit, text="Ajuste").grid(row=0, column=0, sticky="w")
        self.fit_var = tk.StringVar(value="crop")
        ttk.Combobox(fit, textvariable=self.fit_var, width=10, state="readonly",
                     values=["crop", "blur", "contain", "stretch"]).grid(
            row=0, column=1, sticky="w", padx=PAD)
        ttk.Label(fit, text="blur = horizontal em quadro vertical sem cortar",
                  foreground="#666").grid(row=0, column=2, sticky="w")

        self.zoom_var = tk.DoubleVar(value=1.0)
        self.panx_var = tk.DoubleVar(value=0.0)
        self.pany_var = tk.DoubleVar(value=0.0)
        self._slider(fit, 1, "Zoom", self.zoom_var, 0.1, 3.0)
        self._slider(fit, 2, "Horizontal", self.panx_var, -1.0, 1.0)
        self._slider(fit, 3, "Vertical", self.pany_var, -1.0, 1.0)

        # --- extras
        extra = ttk.LabelFrame(left, text="Extras", padding=PAD)
        extra.grid(row=2, column=0, sticky="ew", pady=(0, PAD))

        self.loop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(extra, text="Costurar o loop",
                        variable=self.loop_var).grid(row=0, column=0, sticky="w")
        self.loopsec_var = tk.StringVar(value="1.5")
        ttk.Entry(extra, textvariable=self.loopsec_var, width=6).grid(
            row=0, column=1, padx=PAD)
        ttk.Label(extra, text="segundos", foreground="#666").grid(
            row=0, column=2, sticky="w")

        ttk.Label(extra, text="Cortar em").grid(row=1, column=0, sticky="w",
                                                pady=(6, 0))
        self.seconds_var = tk.StringVar()
        ttk.Entry(extra, textvariable=self.seconds_var, width=6).grid(
            row=1, column=1, padx=PAD, pady=(6, 0))
        ttk.Label(extra, text="segundos (vazio = video inteiro)",
                  foreground="#666").grid(row=1, column=2, sticky="w", pady=(6, 0))

        # --- acoes
        actions = ttk.Frame(left)
        actions.grid(row=3, column=0, sticky="ew")
        actions.columnconfigure((0, 1), weight=1)

        self.btn_preview = ttk.Button(actions, text="Ver previa (rapido)",
                                      command=lambda: self.start(preview=True))
        self.btn_preview.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_build = ttk.Button(actions, text="Gerar video",
                                    command=lambda: self.start(preview=False))
        self.btn_build.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.btn_push = ttk.Button(actions, text="Enviar pro celular",
                                   command=self.do_push)
        self.btn_push.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self.btn_notoast = ttk.Button(
            actions, text="Esconder o aviso de resolucao no celular",
            command=self.do_notoast)
        self.btn_notoast.grid(row=2, column=0, columnspan=2, sticky="ew",
                              pady=(6, 0))

        self.btn_update = ttk.Button(
            actions, text="Procurar atualizacao", command=self.do_update)
        self.btn_update.grid(row=3, column=0, columnspan=2, sticky="ew",
                             pady=(6, 0))

        # --- direita: previa + log
        right = ttk.Frame(root, padding=(0, 0, PAD, PAD))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)

        box = ttk.LabelFrame(right, text="Previa", padding=PAD)
        box.grid(row=0, column=0, sticky="nsew", pady=(0, PAD))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(
            box, anchor="center",
            text="Escolha um video e clique em\n\"Ver previa\"",
            foreground="#666")
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        logbox = ttk.LabelFrame(right, text="Mensagens", padding=PAD)
        logbox.grid(row=1, column=0, sticky="nsew")
        logbox.columnconfigure(0, weight=1)
        logbox.rowconfigure(0, weight=1)
        self.log = tk.Text(logbox, height=8, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(logbox, command=self.log.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=bar.set)

        self.status = ttk.Label(root, text="", padding=(PAD, 0, PAD, PAD),
                                foreground="#666")
        self.status.grid(row=2, column=0, columnspan=2, sticky="w")

    def _slider(self, parent, row, label, var, lo, hi):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w",
                                          pady=(6, 0))
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var,
                          orient="horizontal")
        scale.grid(row=row, column=1, sticky="ew", padx=PAD, pady=(6, 0))
        readout = ttk.Label(parent, width=6)
        readout.grid(row=row, column=2, sticky="w", pady=(6, 0))

        def update(*_):
            readout.configure(text="{:.2f}".format(var.get()))
        var.trace_add("write", update)
        update()

    # --------------------------------------------------------------- ajuda

    def say(self, msg=""):
        self.log_queue.put(msg)

    def _pump_log(self):
        """Drena a fila na thread da interface - Tk nao aceita outra thread."""
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(80, self._pump_log)

    def set_busy(self, busy, status=""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_preview, self.btn_build, self.btn_push,
                    self.btn_notoast, self.btn_update):
            btn.configure(state=state)
        self.status.configure(text=status)

    def check_tools(self):
        try:
            vcam.find_ffmpeg()
        except vcam.Fail:
            messagebox.showerror(
                "ffmpeg nao encontrado",
                "O ffmpeg precisa estar instalado e no PATH.\n\n"
                "Sem ele nao da pra converter video.")
            return
        if not vcam.find_adb(required=False):
            self.say("[!] adb nao encontrado - da pra gerar o video, mas nao")
            self.say("    enviar pro celular. Copie out/virtual.mp4 na mao para")
            self.say("    a pasta DCIM/Camera1 do aparelho.")
        self.say("Pronto. Escolha um video para comecar.")

    def pick_video(self):
        path = filedialog.askopenfilename(
            title="Escolha o video",
            filetypes=[("Videos", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
                       ("Todos", "*.*")])
        if path:
            self.video_var.set(path)
            self.show_source_info(path)

    def show_source_info(self, path):
        try:
            info = vcam.probe(path)
        except vcam.Fail as exc:
            self.say("[x] " + str(exc))
            return
        self.say("Video: {}x{}  {}fps  {}s".format(
            info["width"], info["height"], info["fps"], info["duration"]))

    def on_facing(self):
        self.flip_var.set(self.facing_var.get() == "front")

    def _float(self, text, name, default=None):
        text = (text or "").strip()
        if not text:
            return default
        try:
            return float(text.replace(",", "."))
        except ValueError:
            raise vcam.Fail("{}: '{}' nao e um numero".format(name, text))

    # -------------------------------------------------------------- acoes

    def start(self, preview):
        if self.busy:
            return
        video = self.video_var.get().strip()
        if not video:
            messagebox.showinfo("Falta o video", "Escolha um video primeiro.")
            return
        self.set_busy(True, "Preview..." if preview else "Gerando video...")
        threading.Thread(target=self._work, args=(video, preview),
                         daemon=True).start()

    def _work(self, video, preview):
        try:
            src = Path(video)
            if not src.exists():
                raise vcam.Fail("arquivo nao encontrado: " + str(src))

            w, h = vcam.parse_res(self.res_var.get())
            zoom = round(self.zoom_var.get(), 3)
            pan_x = round(self.panx_var.get(), 3)
            pan_y = round(self.pany_var.get(), 3)
            seconds = self._float(self.seconds_var.get(), "Cortar em")
            fade = (self._float(self.loopsec_var.get(), "Costurar o loop")
                    if self.loop_var.get() else None)
            if seconds is not None and seconds <= 0:
                raise vcam.Fail("'Cortar em' precisa ser maior que zero")
            if fade is not None and fade <= 0:
                raise vcam.Fail("'Costurar o loop' precisa ser maior que zero")

            graph = vcam.build_filters(
                w, h, self.facing_var.get(), int(self.rotate_var.get()),
                self.flip_var.get(), fit=self.fit_var.get(), zoom=zoom,
                pan_x=pan_x, pan_y=pan_y)

            vcam.OUT.mkdir(parents=True, exist_ok=True)
            shot = vcam.OUT / "preview.png"
            info = vcam.probe(src)

            if preview:
                at = min(1.0, info["duration"] / 2)
                vcam.run([vcam.find_ffmpeg(), "-y", "-ss", str(at),
                          "-i", str(src), "-vf", graph,
                          "-frames:v", "1", str(shot)])
                self.say("[ok] previa gerada")
            else:
                final = vcam.OUT / vcam.VIDEO_NAME
                stage = vcam.OUT / "_stage.mp4" if fade else final
                try:
                    vcam.encode(src, stage, graph, 30, seconds=seconds)
                    if fade:
                        self.say("Costurando o loop...")
                        vcam.seamless_loop(stage, final, fade)
                finally:
                    if fade:
                        stage.unlink(missing_ok=True)
                out = vcam.probe(final)
                vcam.grab_frame(final, shot, at=min(1.0, out["duration"] / 2))
                self.say("[ok] video: {}".format(final))
                self.say("     {}x{} {}s".format(
                    out["width"], out["height"], out["duration"]))

                cfg = vcam.load_config()
                cfg.update({"res": self.res_var.get(),
                            "facing": self.facing_var.get()})
                vcam.save_config(cfg)

            self.root.after(0, self._show_preview, shot)
        except vcam.Fail as exc:
            self.say("[x] " + str(exc))
        except Exception as exc:                    # nao deixa a janela travar
            self.say("[x] erro inesperado: {}: {}".format(
                type(exc).__name__, exc))
        finally:
            self.root.after(0, self.set_busy, False, "")

    def _show_preview(self, path):
        try:
            image = tk.PhotoImage(file=str(path))
        except tk.TclError as exc:
            self.say("[!] nao consegui exibir a previa: " + str(exc))
            return
        # PhotoImage so reduz por fatores inteiros; acha o menor que cabe.
        box_w = max(self.preview_label.winfo_width(), 240)
        box_h = max(self.preview_label.winfo_height(), 240)
        factor = 1
        while (image.width() // factor > box_w
               or image.height() // factor > box_h):
            factor += 1
            if factor > 16:
                break
        if factor > 1:
            image = image.subsample(factor, factor)
        self.preview_image = image                  # segura a referencia
        self.preview_label.configure(image=image, text="")

    def do_push(self):
        if self.busy:
            return
        if not (vcam.OUT / vcam.VIDEO_NAME).exists():
            messagebox.showinfo("Nada para enviar",
                                "Gere o video primeiro.")
            return
        self.set_busy(True, "Enviando...")
        threading.Thread(target=self._push_work, daemon=True).start()

    def _push_work(self):
        try:
            cfg = vcam.load_config()
            serial = vcam.require_device(cfg)
            video = vcam.OUT / vcam.VIDEO_NAME
            size = video.stat().st_size
            sent = 0
            for folder in vcam.resolve_dirs(cfg["pkg"],
                                            cfg.get("module", "auto")):
                remote = folder + "/" + vcam.VIDEO_NAME
                vcam.adb(["shell", "mkdir", "-p", folder], check=False,
                         serial=serial)
                proc = vcam.adb(["push", str(video), remote], check=False,
                                serial=serial)
                if proc.returncode == 0 and vcam.remote_size(
                        remote, serial) in (None, size):
                    self.say("[ok] " + folder)
                    sent += 1
                else:
                    self.say("[!]  " + folder + "  (inacessivel)")
            if not sent:
                raise vcam.Fail("nenhum caminho aceitou o arquivo")
            self.say("Abra o app no celular e ligue a camera.")
        except vcam.Fail as exc:
            self.say("[x] " + str(exc))
        except Exception as exc:
            self.say("[x] erro inesperado: {}: {}".format(
                type(exc).__name__, exc))
        finally:
            self.root.after(0, self.set_busy, False, "")

    def do_update(self):
        if self.busy:
            return
        self.set_busy(True, "Procurando atualizacao...")
        threading.Thread(target=self._update_work, daemon=True).start()

    def _update_work(self):
        try:
            cfg = vcam.load_config()
            # Primeiro so consulta: o usuario decide se quer baixar.
            found, manifest = vcam.do_update(cfg, check_only=True,
                                             log=self.say)
            if not found:
                return
            self.root.after(0, self._confirm_update, manifest)
        except vcam.Fail as exc:
            self.say("[x] " + str(exc))
        except Exception as exc:
            self.say("[x] erro inesperado: {}: {}".format(
                type(exc).__name__, exc))
        finally:
            self.root.after(0, self.set_busy, False, "")

    def _confirm_update(self, manifest):
        texto = "Versao {} disponivel (voce tem {}).".format(
            manifest["version"], vcam.__version__)
        if manifest.get("notes"):
            texto += "\n\nMudancas:\n" + str(manifest["notes"])
        texto += "\n\nBaixar e instalar agora?\nO programa vai fechar e reabrir."
        if not messagebox.askyesno("Atualizacao", texto):
            self.say("Atualizacao adiada.")
            return
        self.set_busy(True, "Baixando...")
        threading.Thread(target=self._apply_update, daemon=True).start()

    def _apply_update(self):
        try:
            def progress(done, total):
                if total:
                    self.root.after(
                        0, self.status.configure,
                        {"text": "Baixando... {}%".format(
                            int(done * 100 / total))})

            cfg = vcam.load_config()
            vcam.do_update(cfg, check_only=False, progress=progress,
                           log=self.say)
            self.root.after(0, self.root.destroy)   # o .bat espera fechar
        except vcam.Fail as exc:
            self.say("[x] " + str(exc))
            self.root.after(0, self.set_busy, False, "")
        except Exception as exc:
            self.say("[x] erro inesperado: {}: {}".format(
                type(exc).__name__, exc))
            self.root.after(0, self.set_busy, False, "")

    def do_notoast(self):
        if self.busy:
            return
        self.set_busy(True, "Configurando...")
        threading.Thread(target=self._notoast_work, daemon=True).start()

    def _notoast_work(self):
        try:
            cfg = vcam.load_config()
            serial = vcam.require_device(cfg)
            vcam.OUT.mkdir(parents=True, exist_ok=True)
            blank = vcam.OUT / "_flag.jpg"
            vcam.tiny_jpg(blank)
            try:
                for folder in vcam.resolve_dirs(cfg["pkg"],
                                                cfg.get("module", "auto")):
                    vcam.adb(["shell", "mkdir", "-p", folder], check=False,
                             serial=serial)
                    vcam.adb(["push", str(blank), folder + "/no_toast.jpg"],
                             check=False, serial=serial)
                self.say("[ok] aviso de resolucao escondido")
            finally:
                blank.unlink(missing_ok=True)
        except vcam.Fail as exc:
            self.say("[x] " + str(exc))
        except Exception as exc:
            self.say("[x] erro inesperado: {}: {}".format(
                type(exc).__name__, exc))
        finally:
            self.root.after(0, self.set_busy, False, "")


def ensure_console():
    """Reata a saida ao console do terminal que chamou o exe.

    Empacotado com --windowed, o Windows nao da console ao processo e
    sys.stdout fica None - qualquer print quebraria o modo linha de comando.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.AttachConsole(-1)  # console do processo pai
    except Exception:
        pass
    import os
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open("CONOUT$", "w", encoding="utf-8"))
            except OSError:
                setattr(sys, name, open(os.devnull, "w"))


def main():
    # Com argumentos, comporta-se como o vcam.py de linha de comando.
    if len(sys.argv) > 1:
        ensure_console()
        return vcam.main()

    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    app = App(root)
    vcam.say = app.say          # redireciona as mensagens do vcam para o log
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
