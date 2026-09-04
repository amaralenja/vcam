# vcam

Pipeline de câmera virtual para Android — converte um vídeo para o formato
exato que o módulo VCAM espera e manda pro celular, sem você decorar comando
de ffmpeg nem caminho de pasta.

Feito pra usar junto com **LSPatch** (sem root, sem desbloquear bootloader).

---

## Pré-requisitos

**No PC:**
- `ffmpeg` no PATH ✅ (já instalado)
- Python 3.8+ ✅ (já instalado)
- `adb` — instala com `python vcam.py setup-adb`

**No celular:**
- Shizuku ativo
- LSPatch instalado
- App-alvo já modificado com o módulo VCAM embutido
- Depuração sem fio ligada

---

## Versão .exe (janela, sem linha de comando)

Já tem um pronto em `dist/vcam.exe`. É só dar dois cliques.

A janela tem tudo: escolher o vídeo, resolução, lente, girar, espelhar,
enquadramento com sliders de zoom e posição, costura do loop, botão de
prévia rápida e botão de enviar pro celular.

O `virtual.mp4` e o `preview.jpg` saem numa pasta `out/` **ao lado do .exe**.

### Refazendo o .exe

```bash
python -m pip install pyinstaller
python build_exe.py
```

Por padrão o executável usa o **ffmpeg do PATH** — some 10 MB. Para gerar
uma versão que roda em qualquer PC Windows sem instalar nada:

```bash
python build_exe.py --with-ffmpeg
```

Fica bem maior, mas embute o ffmpeg junto.

### O .exe também aceita linha de comando

Se passar argumentos, ele vira o CLI em vez de abrir a janela:

```bash
vcam.exe build meuvideo.mp4 --res 640x480 --fit blur
```

---

## Atualização automática

O `.exe` tem um botão **Procurar atualização**. Ele consulta um manifesto
JSON na internet, compara versões e, se houver novidade, baixa e se
substitui sozinho — sem reinstalar nada. Funciona em qualquer cópia, em
qualquer PC.

### Configurando (uma vez só)

**1.** No `vcam.py`, preenche a constante:

```python
UPDATE_URL = "https://.../update.json"
```

Tem que ser **https**. O programa recusa http.

**2.** Hospeda em algum lugar estável. **GitHub Releases** é o mais prático
e gratuito: cria um repositório, publica uma release e usa a URL do arquivo.

### Publicando uma versão nova

**1.** Sobe o número em `vcam.py`:

```python
__version__ = "1.1.0"
```

**2.** Gera:

```bash
python build_exe.py --download-url https://.../vcam.exe --notes "o que mudou"
```

Isso cria `dist/vcam.exe` e `dist/update.json`, já com o SHA-256 calculado.

**3.** Publica os **dois** arquivos nas URLs correspondentes.

Pronto. Quem tiver qualquer cópia antiga vai ver a atualização no botão.

### Segurança

O programa **verifica o SHA-256** do arquivo baixado antes de trocar
qualquer coisa. Se não bater, ele apaga o download e cancela. Sem isso, um
servidor comprometido conseguiria fazer o seu PC executar qualquer binário.

Dois avisos honestos:

- Distribuir um programa que se auto-atualiza significa que quem receber
  está confiando em tudo que você publicar naquela URL, para sempre. Não é
  um detalhe pequeno — é acesso contínuo ao PC da pessoa.
- Executáveis feitos com PyInstaller costumam disparar falso positivo no
  Windows Defender e no SmartScreen, e a auto-atualização piora isso. Quem
  receber vai ver aviso de "aplicativo não reconhecido".

### Sobrescrevendo sem recompilar

Dá pra apontar uma cópia já distribuída para outro servidor sem gerar exe
novo: põe um `vcam.json` ao lado dele com

```json
{"update_url": "https://outro-servidor/update.json"}
```

---

## Primeira vez

```bash
python vcam.py setup-adb
python vcam.py doctor
```

Conecta no celular (pega o IP:PORTA em *Opções do desenvolvedor → Depuração sem fio*):

```bash
python vcam.py connect --pair 192.168.0.10:37000
python vcam.py connect 192.168.0.10:41234
```

O `--pair` só é necessário na primeira vez. A porta de pareamento e a de
conexão são **diferentes** — a tela do celular mostra as duas.

---

## Qual módulo você instalou?

Cada módulo VCAM lê de uma pasta diferente:

| Módulo | Pasta | Streaming |
|---|---|---|
| VCAM-Revise / com.example.vcam / xCam | `DCIM/Camera1` e `Android/data/<pkg>/files/Camera1` | Não |
| XVirtualCamera | `Android/data/<pkg>/cache` | **Sim** |

**Você não precisa saber qual é.** Por padrão a ferramenta escreve em
**todos** os caminhos — custa alguns MB e sempre acerta.

Se quiser confirmar (abre a câmera no app modificado **uma vez** antes, pra
o módulo criar a pasta dele):

```bash
python vcam.py detect --save
```

Ou força na mão: `--module vcam` ou `--module xvirtual`.

---

## O ciclo normal

**1. Converte e manda:**

```bash
python vcam.py go meuvideo.mp4
```

**2. Confere o preview** em `out/preview.jpg` antes de qualquer coisa.
Rosto na vertical? Espelhamento natural? Não cortou a cabeça?

**3. Abre o app modificado**, entra numa call de vídeo sozinho, e **anota a
resolução que aparece no toast**.

**4. Reconverte com a resolução certa:**

```bash
python vcam.py go meuvideo.mp4 --res 640x480
```

Ela fica salva em `vcam.json` — nas próximas vezes não precisa repetir.

**5. Antes da call de verdade, esconde o toast:**

```bash
python vcam.py flags --no-toast
```

> Sem isso, seu amigo vê o aviso de resolução aparecer na tela. 😅

---

## Corrigindo a imagem

| Problema | Solução |
|---|---|
| Deitado | `--rotate 90` (ou `180`, `270`) |
| Espelhado errado | `--flip` ou `--no-flip` |
| Borrado | resolução errada — usa a do toast |
| Tela preta | pasta errada — roda `python vcam.py detect` |
| Câmera real aparece | módulo não marcado no LSPatch |

---

## Vídeo horizontal em câmera vertical

A câmera pede quadro vertical, mas seu vídeo é horizontal. Quatro jeitos de
resolver:

```bash
python vcam.py build meuvideo.mp4 --res 720x1280 --fit blur
```

| `--fit` | O que faz |
|---|---|
| `crop` (padrão) | Preenche e corta as sobras. Nada de tarja, mas perde as laterais |
| `blur` | Vídeo inteiro no meio, fundo borrado em cima e embaixo. **Não corta nada** |
| `contain` | Vídeo inteiro com tarja preta |
| `stretch` | Estica pra caber. Deforma o rosto — evita |

Pra uma trollagem, `blur` costuma ser o mais convincente: não perde nada da
imagem e não tem tarja preta denunciando.

### Mexendo o enquadramento

```bash
python vcam.py build meuvideo.mp4 --res 720x1280 --pan-y -0.4 --zoom 1.2
```

| Opção | Faixa | Efeito |
|---|---|---|
| `--pan-x` | -1 a 1 | -1 esquerda, 0 centro, 1 direita |
| `--pan-y` | -1 a 1 | -1 topo, 0 centro, 1 baixo |
| `--zoom` | 0.1 a 5 | >1 aproxima, <1 afasta |
| `--blur` | 0 a 100 | intensidade do fundo no `--fit blur` |

Se seu rosto está na parte de cima do vídeo, `--pan-y -0.4` puxa o
enquadramento pra cima.

### Ajustando rápido

Codificar o vídeo inteiro a cada tentativa é lento. Use:

```bash
python vcam.py build meuvideo.mp4 --res 720x1280 --pan-y -0.4 --preview-only
```

Gera **só** o `out/preview.jpg`, em instantes. Vai ajustando os valores até
gostar, depois roda de novo sem o `--preview-only`.

---

## Modo streaming — vídeo fica no PC

Em vez de copiar o arquivo pro celular, o PC serve o vídeo por HTTP e o
celular só aponta pra ele.

```bash
python vcam.py build meuvideo.mp4 --res 640x480
python vcam.py serve
```

Deixa a janela aberta durante a call. Pra trocar o vídeo, roda o `build` em
outro terminal — o celular pega a versão nova na próxima vez que a câmera
abrir. Sem re-enviar nada.

Pra voltar ao arquivo local:

```bash
python vcam.py unstream
```

### Não precisa de domínio nem de hospedagem

E `localhost` **não** funciona: no celular ele aponta pro próprio celular.

Por padrão o `serve` monta um túnel com **`adb reverse`**. O celular chama
o próprio `127.0.0.1:8000` e o adb encaminha até o PC. Com isso:

- não depende do IP da rede (que muda quando o roteador reatribui)
- não depende do Firewall do Windows
- **com cabo USB, não depende do Wi-Fi** — fica tão confiável quanto arquivo local

Se o túnel falhar, ele cai sozinho pro IP da rede local. Aí valem as regras
chatas: mesmo Wi-Fi e porta liberada no Firewall. Pra forçar esse modo:

```bash
python vcam.py serve --lan
```

### Requisito

Streaming só funciona com módulo que aceite fonte em rede — o
**XVirtualCamera** suporta (`http`, `rtsp`, `rtmp`, `rtp`). O VCAM-Revise
só lê arquivo local, e aí você usa o `push`.

### Streaming ou push?

| | `push` | `serve` (USB) | `serve` (Wi-Fi) |
|---|---|---|---|
| Pode cair no meio da call | Não | Não | **Sim** |
| Trocar de vídeo | Re-enviar | Instantâneo | Instantâneo |
| Ocupa espaço no celular | Sim | Não | Não |
| Módulo | Qualquer um | Só com rede | Só com rede |

**`serve` com o cabo USB plugado é o melhor dos dois mundos**: troca de
vídeo instantânea e sem risco de queda, porque o túnel não passa pelo Wi-Fi.

Se for usar sem cabo, prefira o `push` para a call de verdade — Wi-Fi
oscilando no meio do streaming trava a câmera e acaba a brincadeira.

---

## Loop sem emenda

O maior denunciador é o corte do loop. Isso costura o fim no começo:

```bash
python vcam.py go meuvideo.mp4 --loop-fade 1.5
```

Usa 1 a 2 segundos. O vídeo precisa ser mais longo que o dobro do fade.

---

## Todos os comandos

```
doctor       checa ffmpeg, adb, aparelho e config
setup-adb    baixa o adb oficial do Google
connect      conecta no celular por Wi-Fi
probe        mostra resolução/fps/duração de um vídeo
build        converte (não envia)
push         envia o convertido
go           build + push
detect       descobre qual módulo VCAM está instalado
flags        liga/desliga os arquivos de controle do VCAM
serve        roda o vídeo do PC e aponta o celular pra ele
unstream     volta a usar o arquivo local do celular
clean        apaga tudo do celular  (--purge remove também as pastas)
version      mostra a versão e a URL de atualização
update       baixa e instala a versão nova (--check só avisa)
```

Opções úteis do `build`/`go`:

```
--res 640x480       resolução alvo
--facing front|back câmera alvo (front espelha por padrão)
--rotate 90         gira a imagem
--flip / --no-flip  força ou desliga o espelhamento
--seconds 180       corta pra N segundos
--start 00:01:30    começa em tal ponto
--loop-fade 1.5     costura o loop
--fps 30            frames por segundo
--fit crop|blur|contain|stretch   horizontal em quadro vertical
--zoom 1.2          aproxima (>1) ou afasta (<1)
--pan-x / --pan-y   move o enquadramento (-1 a 1)
--blur 20           intensidade do fundo no --fit blur
--preview-only      só o preview, sem codificar (rápido)
```

E nos comandos que falam com o celular:

```
--pkg com.discord           app alvo
--module auto|vcam|xvirtual layout do módulo (padrão: auto)
```

---

## Desfazendo tudo

```bash
python vcam.py clean
```

Remove os arquivos do celular. Pra reverter de vez, é só desinstalar o app
modificado — o sistema nunca foi tocado.

---

## Notas

- O VCAM é **só vídeo**. Seu microfone real continua ao vivo — é isso que faz
  a coisa funcionar: você conversa normal enquanto o vídeo roda.
- Grave-se **mais ouvindo do que falando**, senão a boca não bate.
- Não use no WhatsApp: ele detecta cliente modificado e bane o número.
  Discord (`com.discord`) e Telegram (`org.telegram.messenger`) são seguros.
- Depois de reiniciar o celular, reative o Shizuku antes de usar o `push`.
