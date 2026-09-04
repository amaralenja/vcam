#!/usr/bin/env python3
"""
Janela do vcam - camera virtual para Android.

Tres abas: Video (converter e enviar), Celular (conectar e configurar) e
Tutorial (passo a passo). Roda direto (python vcam_gui.py) ou empacotado
como .exe; com argumentos de linha de comando, cai no modo CLI do vcam.py.
"""

import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace

import vcam

PAD = 8


# ------------------------------------------------------------------ tutorial

TUTORIAL_S21 = [
    ("1. Opcoes do desenvolvedor", [
        "No celular: Ajustes > Sobre o telefone > Informacoes do software.",
        "Toque 7 vezes seguidas em \"Numero da versao\".",
        "Digite seu PIN. As \"Opcoes do desenvolvedor\" aparecem no fim",
        "da lista de Ajustes.",
    ]),
    ("2. Depuracao sem fio", [
        "Entre em Ajustes > Opcoes do desenvolvedor.",
        "Ligue \"Depuracao sem fio\" (o celular precisa estar no Wi-Fi).",
        "IMPORTANTE: PC e celular no MESMO Wi-Fi.",
    ]),
    ("3. Shizuku", [
        "Instale o Shizuku pela Play Store.",
        "Em Depuracao sem fio, toque em \"Parear com codigo\".",
        "Aparece um IP:PORTA e um codigo de 6 digitos.",
        "Aqui na aba Celular, cole esse IP:PORTA e o codigo e clique",
        "\"Parear\". Depois abra o Shizuku e toque em INICIAR.",
        "OBS: o Shizuku para quando o celular reinicia - refaca este passo.",
    ]),
    ("4. Nao deixar a Samsung matar os apps", [
        "Ajustes > Bateria > Limites de uso em segundo plano.",
        "Garanta que Shizuku e LSPatch NAO estao em \"Apps adormecidos\".",
        "Senao eles morrem no meio do processo.",
    ]),
    ("5. LSPatch", [
        "Baixe o LSPatch (link no botao abaixo, use o release oficial).",
        "Instale, abra e conceda acesso ao Shizuku.",
    ]),
    ("6. Modulo VCAM", [
        "Baixe o modulo android_VCAM-Revise (botao abaixo).",
        "Ele tem Camera1 + Camera2 - o Discord usa Camera2.",
        "Instale o APK. Sozinho ele nao faz nada, e so o pacote.",
    ]),
    ("7. Patch no Discord", [
        "Instale o Discord normal pela Play Store, se ainda nao tiver.",
        "No LSPatch: aba Apps > + > escolha \"app instalado\" > Discord.",
        "Modo Local > Iniciar patch.",
        "Desinstale o Discord original.",
        "Volte ao LSPatch e instale o Discord modificado de la.",
        "Segure o dedo nele > Escopo de modulos > marque o VCAM.",
        "ESSE passo do escopo e o que mais gente esquece.",
    ]),
    ("8. Conectar aqui e testar", [
        "Clique em \"Instalar/atualizar adb\" na aba Celular.",
        "Pluigue o cabo USB (mais estavel) e autorize a depuracao na tela,",
        "ou use o pareamento por Wi-Fi do passo 3.",
        "Clique em \"Verificar conexao\": tem que aparecer o aparelho.",
        "Gere um video aqui, clique \"Enviar pro celular\", abra o Discord",
        "modificado, entre numa call sozinho e ligue a camera.",
        "Anote a resolucao que aparecer no toast e ponha la em cima.",
    ]),
    ("9. Antes da brincadeira", [
        "Clique em \"Esconder aviso de resolucao\" para o toast nao aparecer.",
        "Seu MICROFONE continua real e ao vivo - fale normal.",
        "Grave o video mais ouvindo do que falando, senao a boca nao bate.",
    ]),
]

LINKS = {
    "LSPatch (release oficial)":
        "https://github.com/JingMatrix/LSPatch/releases",
    "Modulo VCAM-Revise":
        "https://github.com/Cross2pro/android_VCAM-Revise",
    "Shizuku (Play Store)":
        "https://play.google.com/store/apps/details?id=moe.shizuku.privileged.api",
}


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("vcam {} - camera virtual".format(vcam.__version__))
        self.root.minsize(940, 660)

        self.cfg = vcam.load_config()
        self.busy = False
        self.log_queue = queue.Queue()
        self.preview_image = None
        self.buttons = []

        self._build()
        self._pump_log()
        self.check_tools()
        threading.Thread(target=self._silent_update_check, daemon=True).start()
        self._poll_device()

    # ---------------------------------------------------------------- layout

    def _build(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        nb = ttk.Notebook(self.root)
        nb.grid(row=0, column=0, sticky="nsew")

        self.tab_video = ttk.Frame(nb, padding=PAD)
        self.tab_phone = ttk.Frame(nb, padding=PAD)
        self.tab_help = ttk.Frame(nb, padding=PAD)
        nb.add(self.tab_video, text="  Video  ")
        nb.add(self.tab_phone, text="  Celular  ")
        nb.add(self.tab_help, text="  Tutorial  ")

        self._build_video(self.tab_video)
        self._build_phone(self.tab_phone)
        self._build_help(self.tab_help)

        # barra de status compartilhada
        bar = ttk.Frame(self.root)
        bar.grid(row=1, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        self.dev_dot = tk.Label(bar, text="●", fg="#c0392b")
        self.dev_dot.grid(row=0, column=0, padx=(PAD, 4), pady=4)
        self.dev_label = ttk.Label(bar, text="Nenhum celular conectado")
        self.dev_label.grid(row=0, column=1, sticky="w")
        self.status = ttk.Label(bar, text="", foreground="#666")
        self.status.grid(row=0, column=2, sticky="e", padx=PAD)

    # ------------------------------------------------------------ aba video

    def _build_video(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, PAD))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Video:").grid(row=0, column=0, sticky="w")
        self.video_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.video_var).grid(
            row=0, column=1, sticky="ew", padx=PAD)
        ttk.Button(top, text="Procurar...", command=self.pick_video).grid(
            row=0, column=2)

        left = ttk.Frame(tab)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, PAD))
        left.columnconfigure(0, weight=1)

        # camera
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

        # enquadramento
        fit = ttk.LabelFrame(left, text="Enquadramento", padding=PAD)
        fit.grid(row=1, column=0, sticky="ew", pady=(0, PAD))
        fit.columnconfigure(1, weight=1)
        ttk.Label(fit, text="Ajuste").grid(row=0, column=0, sticky="w")
        self.fit_var = tk.StringVar(value="crop")
        cb = ttk.Combobox(fit, textvariable=self.fit_var, width=10,
                          state="readonly",
                          values=["crop", "blur", "contain", "stretch"])
        cb.grid(row=0, column=1, sticky="w", padx=PAD)
        cb.bind("<<ComboboxSelected>>", lambda e: self.auto_preview())
        ttk.Label(fit, text="blur = horizontal em quadro vertical sem cortar",
                  foreground="#666").grid(row=0, column=2, sticky="w")
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.panx_var = tk.DoubleVar(value=0.0)
        self.pany_var = tk.DoubleVar(value=0.0)
        self._slider(fit, 1, "Zoom", self.zoom_var, 0.1, 3.0)
        self._slider(fit, 2, "Horizontal", self.panx_var, -1.0, 1.0)
        self._slider(fit, 3, "Vertical", self.pany_var, -1.0, 1.0)

        self.autoprev_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(fit, text="Atualizar previa ao soltar o slider",
                        variable=self.autoprev_var).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # extras
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

        # acoes
        act = ttk.Frame(left)
        act.grid(row=3, column=0, sticky="ew")
        act.columnconfigure((0, 1), weight=1)
        self._btn(act, "Ver previa (rapido)",
                  lambda: self.start(preview=True), 0, 0)
        self._btn(act, "Gerar video", lambda: self.start(preview=False), 0, 1)
        self._btn(act, "Enviar pro celular", self.do_push, 1, 0, span=2)

        self.progress = ttk.Progressbar(act, mode="determinate", maximum=100)
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew",
                           padx=2, pady=(6, 0))
        self.progress.grid_remove()   # so aparece durante a conversao
        self.prog_label = ttk.Label(act, text="", foreground="#666")
        self.prog_label.grid(row=3, column=0, columnspan=2, sticky="w", padx=2)

        # direita: previa
        box = ttk.LabelFrame(tab, text="Previa", padding=PAD)
        box.grid(row=1, column=1, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(
            box, anchor="center", foreground="#666",
            text="Escolha um video e clique em\n\"Ver previa\"")
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        # log embaixo
        self._build_log(tab, row=2)

    # ----------------------------------------------------------- aba celular

    def _build_phone(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(5, weight=1)

        adb = ttk.LabelFrame(tab, text="1. Ferramenta de conexao (adb)",
                             padding=PAD)
        adb.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        adb.columnconfigure(1, weight=1)
        self._btn(adb, "Instalar / atualizar adb", self.do_setup_adb, 0, 0)
        ttk.Label(adb, text="Baixa a ferramenta oficial do Google. So na "
                  "primeira vez.", foreground="#666").grid(
            row=0, column=1, sticky="w", padx=PAD)

        usb = ttk.LabelFrame(tab, text="2a. Conectar por cabo USB (recomendado)",
                             padding=PAD)
        usb.grid(row=1, column=0, sticky="ew", pady=(0, PAD))
        usb.columnconfigure(1, weight=1)
        self._btn(usb, "Verificar conexao", self.do_check_device, 0, 0)
        ttk.Label(usb, text="Pluigue o cabo, autorize a depuracao na tela do "
                  "celular e clique aqui.", foreground="#666").grid(
            row=0, column=1, sticky="w", padx=PAD)

        wifi = ttk.LabelFrame(tab, text="2b. Conectar por Wi-Fi (sem cabo)",
                              padding=PAD)
        wifi.grid(row=2, column=0, sticky="ew", pady=(0, PAD))
        wifi.columnconfigure(1, weight=1)

        ttk.Label(wifi, text="Parear (primeira vez):").grid(
            row=0, column=0, sticky="w")
        pf = ttk.Frame(wifi)
        pf.grid(row=0, column=1, sticky="w", padx=PAD, pady=2)
        ttk.Label(pf, text="IP:PORTA").pack(side="left")
        self.pair_addr = tk.StringVar()
        ttk.Entry(pf, textvariable=self.pair_addr, width=20).pack(
            side="left", padx=4)
        ttk.Label(pf, text="codigo").pack(side="left")
        self.pair_code = tk.StringVar()
        ttk.Entry(pf, textvariable=self.pair_code, width=8).pack(
            side="left", padx=4)
        self._btn(pf, "Parear", self.do_pair, pack=True)

        ttk.Label(wifi, text="Conectar:").grid(row=1, column=0, sticky="w")
        cf = ttk.Frame(wifi)
        cf.grid(row=1, column=1, sticky="w", padx=PAD, pady=2)
        ttk.Label(cf, text="IP:PORTA").pack(side="left")
        self.conn_addr = tk.StringVar()
        ttk.Entry(cf, textvariable=self.conn_addr, width=20).pack(
            side="left", padx=4)
        self._btn(cf, "Conectar", self.do_connect, pack=True)

        ttk.Label(wifi, text="A tela de Depuracao sem fio do celular mostra os "
                  "dois IP:PORTA (o de parear e o de conectar sao diferentes).",
                  foreground="#666").grid(row=2, column=0, columnspan=2,
                                          sticky="w", pady=(4, 0))

        cfgf = ttk.LabelFrame(tab, text="3. Configuracao do app-alvo", padding=PAD)
        cfgf.grid(row=3, column=0, sticky="new", pady=(0, PAD))
        cfgf.columnconfigure(1, weight=1)
        ttk.Label(cfgf, text="App").grid(row=0, column=0, sticky="w")
        self.pkg_var = tk.StringVar(value=self.cfg["pkg"])
        # nome amigavel -> pacote. Apps de videochamada que aceitam bem o
        # cliente modificado.
        self.BUILTIN_APPS = [
            ("Discord", "com.discord"),
            ("Telegram", "org.telegram.messenger"),
            ("Messenger (Facebook)", "com.facebook.orca"),
            ("Signal", "org.thoughtcrime.securesms"),
            ("Google Meet", "com.google.android.apps.tachyon"),
            ("Zoom", "us.zoom.videomeetings"),
            ("Skype", "com.skype.raider"),
            ("Snapchat", "com.snapchat.android"),
            ("Instagram (arisco)", "com.instagram.android"),
        ]
        self.appname_var = tk.StringVar()
        self.appcb = ttk.Combobox(cfgf, textvariable=self.appname_var, width=26,
                                  state="readonly")
        self.appcb.grid(row=0, column=1, sticky="w", padx=PAD)
        self.appcb.bind("<<ComboboxSelected>>", self.on_app_pick)

        ttk.Label(cfgf, text="ou pacote:").grid(row=1, column=0, sticky="w",
                                                pady=(4, 0))
        ent = ttk.Entry(cfgf, textvariable=self.pkg_var, width=30)
        ent.grid(row=1, column=1, sticky="w", padx=PAD, pady=(4, 0))
        self._btn(cfgf, "Salvar na lista", self.do_save_app, 1, 2)
        self._refresh_apps()
        btns = ttk.Frame(cfgf)
        btns.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._btn(btns, "Detectar modulo instalado", self.do_detect, pack=True)
        self._btn(btns, "Esconder aviso de resolucao", self.do_notoast,
                  pack=True)
        self._btn(btns, "Limpar arquivos do celular", self.do_clean, pack=True)

        upd = ttk.LabelFrame(tab, text="4. Programa", padding=PAD)
        upd.grid(row=4, column=0, sticky="ew", pady=(0, PAD))
        upd.columnconfigure(1, weight=1)
        self._btn(upd, "Procurar atualizacao", self.do_update, 0, 0)
        ttk.Label(upd, text="Voce esta na versao " + vcam.__version__,
                  foreground="#666").grid(row=0, column=1, sticky="w", padx=PAD)

    # --------------------------------------------------------- aba tutorial

    def _build_help(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        wrap = ttk.Frame(tab)
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        canvas = tk.Canvas(wrap, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=sb.set)
        inner = ttk.Frame(canvas, padding=(0, 0, PAD, 0))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        head = ttk.Label(
            inner, font=("Segoe UI", 11, "bold"),
            text="Como deixar o Galaxy S21 pronto (Android)")
        head.pack(anchor="w", pady=(0, 2))
        ttk.Label(inner, foreground="#666", wraplength=560, justify="left",
                  text="Sem root, sem formatar. O LSPatch modifica so o app "
                       "escolhido. Siga na ordem.").pack(anchor="w", pady=(0, PAD))

        for titulo, passos in TUTORIAL_S21:
            f = ttk.LabelFrame(inner, text=titulo, padding=PAD)
            f.pack(fill="x", pady=4)
            ttk.Label(f, justify="left", wraplength=560,
                      text="\n".join(passos)).pack(anchor="w")

        links = ttk.LabelFrame(inner, text="Downloads (abrem no navegador)",
                               padding=PAD)
        links.pack(fill="x", pady=(PAD, 0))
        for nome, url in LINKS.items():
            b = ttk.Button(links, text=nome,
                           command=lambda u=url: webbrowser.open(u))
            b.pack(anchor="w", pady=2)

        ttk.Label(inner, foreground="#c0392b", wraplength=560, justify="left",
                  text="Aviso: cliente modificado e contra os termos do "
                       "Discord; o risco de ban e baixo, mas existe. Nao instale "
                       "app de banco no aparelho com root; aqui e so LSPatch, "
                       "sem root.").pack(anchor="w", pady=(PAD, 0))

    # -------------------------------------------------------------- helpers

    def _build_log(self, parent, row):
        box = ttk.LabelFrame(parent, text="Mensagens", padding=PAD)
        box.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(PAD, 0))
        box.columnconfigure(0, weight=1)
        parent.rowconfigure(row, weight=1)
        self.log = tk.Text(box, height=7, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

    def _slider(self, parent, row, label, var, lo, hi):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w",
                                          pady=(6, 0))
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var,
                          orient="horizontal")
        scale.grid(row=row, column=1, sticky="ew", padx=PAD, pady=(6, 0))
        scale.bind("<ButtonRelease-1>", lambda e: self.auto_preview())
        readout = ttk.Label(parent, width=6)
        readout.grid(row=row, column=2, sticky="w", pady=(6, 0))
        var.trace_add("write",
                      lambda *_: readout.configure(text="{:.2f}".format(var.get())))
        readout.configure(text="{:.2f}".format(var.get()))

    def _btn(self, parent, text, cmd, r=0, c=0, span=1, pack=False):
        b = ttk.Button(parent, text=text, command=cmd)
        if pack:
            b.pack(side="left", padx=(0, 6))
        else:
            b.grid(row=r, column=c, columnspan=span, sticky="ew",
                   padx=2, pady=2)
        self.buttons.append(b)
        return b

    # ------------------------------------------------------------------ log

    def say(self, msg=""):
        self.log_queue.put(msg)

    def _pump_log(self):
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
        for b in self.buttons:
            b.configure(state=state)
        self.status.configure(text=status)

    # --------------------------------------------------------- device watch

    def _poll_device(self):
        threading.Thread(target=self._poll_once, daemon=True).start()
        self.root.after(6000, self._poll_device)

    def _poll_once(self):
        try:
            if not vcam.find_adb(required=False):
                self.root.after(0, self._set_dev, None, "adb nao instalado")
                return
            states = vcam.device_states()
            ready = [s for s, st in states if st == "device"]
            if ready:
                self.root.after(0, self._set_dev, True, ready[0])
            elif states:
                s, st = states[0]
                self.root.after(0, self._set_dev, False,
                                "{} ({})".format(s, st))
            else:
                self.root.after(0, self._set_dev, None, "nenhum conectado")
        except Exception:
            pass

    def _set_dev(self, ok, text):
        color = {True: "#27ae60", False: "#e67e22", None: "#c0392b"}[ok]
        self.dev_dot.configure(fg=color)
        prefix = {True: "Conectado: ", False: "", None: ""}[ok]
        self.dev_label.configure(text=prefix + text)

    # -------------------------------------------------------------- helpers

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
            self.say("[!] adb ainda nao instalado. Na aba Celular, clique em")
            self.say("    \"Instalar / atualizar adb\".")
        self.say("Pronto. Escolha um video para comecar.")

    def _silent_update_check(self):
        try:
            cfg = vcam.load_config()
            if not vcam.update_url(cfg):
                return
            found, man = vcam.do_update(cfg, check_only=True, log=lambda _="": None)
            if found:
                self.say("[!] Versao {} disponivel (voce tem {}). Aba Celular "
                         "> \"Procurar atualizacao\".".format(
                             man["version"], vcam.__version__))
        except Exception:
            pass

    def pick_video(self):
        path = filedialog.askopenfilename(
            title="Escolha o video",
            filetypes=[("Videos", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
                       ("Todos", "*.*")])
        if path:
            self.video_var.set(path)
            try:
                info = vcam.probe(path)
                self.say("Video: {}x{}  {}fps  {}s".format(
                    info["width"], info["height"], info["fps"],
                    info["duration"]))
                self.auto_preview()
            except vcam.Fail as exc:
                self.say("[x] " + str(exc))

    def on_facing(self):
        self.flip_var.set(self.facing_var.get() == "front")
        self.auto_preview()

    def auto_preview(self):
        if self.autoprev_var.get() and self.video_var.get().strip() \
                and not self.busy:
            self.start(preview=True, quiet=True)

    def _float(self, text, name, default=None):
        text = (text or "").strip()
        if not text:
            return default
        try:
            return float(text.replace(",", "."))
        except ValueError:
            raise vcam.Fail("{}: '{}' nao e um numero".format(name, text))

    # ---------------------------------------------------- acoes de video

    def start(self, preview, quiet=False):
        if self.busy:
            return
        video = self.video_var.get().strip()
        if not video:
            if not quiet:
                messagebox.showinfo("Falta o video", "Escolha um video primeiro.")
            return
        self.set_busy(True, "Preview..." if preview else "Gerando video...")
        threading.Thread(target=self._work, args=(video, preview, quiet),
                         daemon=True).start()

    def _work(self, video, preview, quiet=False):
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
                if not quiet:
                    self.say("[ok] previa gerada")
            else:
                final = vcam.OUT / vcam.VIDEO_NAME
                stage = vcam.OUT / "_stage.mp4" if fade else final
                # duracao esperada da saida, para calcular a porcentagem
                total = seconds if seconds else info["duration"]
                self.root.after(0, self._progress_show)
                try:
                    vcam.encode(src, stage, graph, 30, seconds=seconds,
                                total=total, progress=self._on_progress)
                    if fade:
                        self.root.after(0, self._progress_text,
                                        "Costurando o loop...")
                        vcam.seamless_loop(stage, final, fade)
                finally:
                    if fade:
                        stage.unlink(missing_ok=True)
                    self.root.after(0, self._progress_hide)
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
        except Exception as exc:
            self.say("[x] erro inesperado: {}: {}".format(
                type(exc).__name__, exc))
        finally:
            self.root.after(0, self.set_busy, False, "")

    def _progress_show(self):
        self.progress.grid()
        self.progress["value"] = 0
        self.prog_label.configure(text="Convertendo... 0%")

    def _progress_hide(self):
        self.progress.grid_remove()
        self.prog_label.configure(text="")

    def _progress_text(self, text):
        self.prog_label.configure(text=text)

    def _on_progress(self, done, total):
        # chamado da thread do ffmpeg; agenda a atualizacao na thread da UI
        pct = int(done * 100 / total) if total else 0
        self.root.after(0, self._set_progress, pct)

    def _set_progress(self, pct):
        self.progress["value"] = pct
        self.prog_label.configure(text="Convertendo... {}%".format(pct))

    def _show_preview(self, path):
        try:
            image = tk.PhotoImage(file=str(path))
        except tk.TclError as exc:
            self.say("[!] nao consegui exibir a previa: " + str(exc))
            return
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
        self.preview_image = image
        self.preview_label.configure(image=image, text="")

    # --------------------------------------------------- acoes de celular

    def _run_bg(self, status, fn):
        if self.busy:
            return
        self.set_busy(True, status)

        def wrap():
            try:
                fn()
            except vcam.Fail as exc:
                self.say("[x] " + str(exc))
            except Exception as exc:
                self.say("[x] erro inesperado: {}: {}".format(
                    type(exc).__name__, exc))
            finally:
                self.root.after(0, self.set_busy, False, "")
        threading.Thread(target=wrap, daemon=True).start()

    def do_setup_adb(self):
        def job():
            self.say("Baixando o adb do Google (uns 5 MB)...")
            vcam.cmd_setup_adb(SimpleNamespace(force=False))
            self.say("[ok] adb pronto.")
        self._run_bg("Instalando adb...", job)

    def do_check_device(self):
        def job():
            vcam.start_adb_server()
            serial = vcam.require_device(vcam.load_config())
            self.say("[ok] conectado: " + serial)
        self._run_bg("Verificando...", job)

    def do_pair(self):
        addr = self.pair_addr.get().strip()
        code = self.pair_code.get().strip()
        if not addr or not code:
            messagebox.showinfo("Faltam dados",
                                "Preencha o IP:PORTA e o codigo de pareamento.")
            return

        def job():
            self.say(vcam.adb_pair(addr, code))
            self.say("[ok] pareado. Agora preencha o IP:PORTA de conexao e "
                     "clique Conectar.")
        self._run_bg("Pareando...", job)

    def do_connect(self):
        addr = self.conn_addr.get().strip()
        if not addr:
            messagebox.showinfo("Falta o endereco",
                                "Preencha o IP:PORTA de conexao.")
            return

        def job():
            self.say(vcam.adb_connect(addr))
            serial = vcam.require_device(vcam.load_config())
            self.say("[ok] conectado: " + serial)
        self._run_bg("Conectando...", job)

    def _all_apps(self):
        """Apps embutidos + os que o usuario salvou (sem duplicar pacote)."""
        seen = {p for _, p in self.BUILTIN_APPS}
        custom = [(n, p) for n, p in self.cfg.get("apps", [])
                  if p not in seen and not seen.add(p)]
        return self.BUILTIN_APPS + custom

    def _refresh_apps(self):
        apps = self._all_apps()
        self.appcb.configure(values=[n for n, _ in apps])
        cur = self.pkg_var.get() or self.cfg["pkg"]
        name = next((n for n, p in apps if p == cur), None)
        if name:
            self.appname_var.set(name)
        if not self.pkg_var.get():
            self.pkg_var.set(self.cfg["pkg"])

    def on_app_pick(self, _evt=None):
        name = self.appname_var.get()
        pkg = dict((n, p) for n, p in self._all_apps()).get(name)
        if pkg:
            self.pkg_var.set(pkg)

    def do_save_app(self):
        pkg = self.pkg_var.get().strip()
        if not pkg or "." not in pkg:
            messagebox.showinfo(
                "Pacote invalido",
                "Digite o pacote no formato com.exemplo.app no campo "
                "\"ou pacote\".")
            return
        from tkinter import simpledialog
        nome = simpledialog.askstring(
            "Salvar app", "Nome para aparecer na lista:",
            initialvalue=pkg.split(".")[-1].capitalize(), parent=self.root)
        if not nome:
            return
        apps = [a for a in self.cfg.get("apps", []) if a[1] != pkg]
        apps.append([nome, pkg])
        self.cfg["apps"] = apps
        cfg = vcam.load_config()
        cfg["apps"] = apps
        cfg["pkg"] = pkg
        vcam.save_config(cfg)
        self._refresh_apps()
        self.appname_var.set(nome)
        self.say("[ok] app salvo na lista: {} ({})".format(nome, pkg))

    def do_detect(self):
        def job():
            cfg = vcam.load_config()
            cfg["pkg"] = self.pkg_var.get().strip() or "com.discord"
            vcam.save_config(cfg)
            vcam.cmd_detect(SimpleNamespace(pkg=cfg["pkg"], save=True))
        self._run_bg("Detectando modulo...", job)

    def _save_pkg(self):
        cfg = vcam.load_config()
        cfg["pkg"] = self.pkg_var.get().strip() or "com.discord"
        vcam.save_config(cfg)
        return cfg

    def do_push(self):
        if not (vcam.OUT / vcam.VIDEO_NAME).exists():
            messagebox.showinfo("Nada para enviar", "Gere o video primeiro.")
            return

        def job():
            cfg = self._save_pkg()
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
        self._run_bg("Enviando...", job)

    def do_update(self):
        if self.busy:
            return
        self.set_busy(True, "Procurando atualizacao...")
        threading.Thread(target=self._update_work, daemon=True).start()

    def _update_work(self):
        try:
            cfg = vcam.load_config()
            found, manifest = vcam.do_update(cfg, check_only=True,
                                             log=self.say)
            if found:
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
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                "So no .exe",
                "A atualizacao automatica so funciona no vcam.exe.\n"
                "Rodando pelo codigo-fonte, use o git.")
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
            vcam.do_update(vcam.load_config(), check_only=False,
                           progress=progress, log=self.say)
            self.root.after(0, self.root.destroy)   # o .bat espera fechar
        except vcam.Fail as exc:
            self.root.after(0, self._update_failed, str(exc))
        except Exception as exc:
            self.root.after(0, self._update_failed,
                            "{}: {}".format(type(exc).__name__, exc))

    def _update_failed(self, msg):
        self.set_busy(False, "")
        self.say("[x] atualizacao falhou: " + msg)
        link = "https://github.com/amaralenja/vcam/releases/latest"
        messagebox.showerror(
            "Atualizacao falhou",
            "Nao deu para atualizar automaticamente:\n\n" + msg +
            "\n\nQuase sempre e o antivirus mexendo no arquivo baixado.\n"
            "Baixe a versao nova na mao aqui:\n" + link)
        try:
            webbrowser.open(link)
        except Exception:
            pass

    def do_notoast(self):
        def job():
            cfg = self._save_pkg()
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
        self._run_bg("Configurando...", job)

    def do_clean(self):
        if not messagebox.askyesno(
                "Limpar", "Remover os arquivos do vcam do celular?\n"
                "O app volta a usar a camera real."):
            return

        def job():
            cfg = self._save_pkg()
            vcam.cmd_clean(SimpleNamespace(pkg=cfg["pkg"], purge=False))
        self._run_bg("Limpando...", job)


def ensure_console():
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.AttachConsole(-1)
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
    if len(sys.argv) > 1:
        ensure_console()
        return vcam.main()

    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    app = App(root)
    vcam.say = app.say
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
