# MEMORY.md — Cérebro do Projeto MDM Titan

> Este arquivo é o norte do projeto. Contém tudo que precisamos saber para continuar o desenvolvimento sem perder contexto.

---

## O que é o MDM Titan

**Media Download Manager** — sistema web completo para buscar e baixar filmes, séries, animes e desenhos a partir de uma lista M3U/IPTV (`data/dados.txt`). Interface dark premium com Flask + PostgreSQL.

- **URL local:** `http://localhost:3070`
- **Porta:** 3070
- **Stack:** Python 3.10, Flask, PostgreSQL, SQLite in-memory (indexer), TailwindCSS, Lucide Icons
- **Storage de mídia:** `/mnt/media*` (auto-discovery — qualquer mount em `/mnt/media`, `/mnt/media2`, futuros `/mnt/media3`…). UI mostra cards coloridos por % de uso.

---

## Arquitetura de Arquivos

```
/opt/mdm/
├── app.py              # Servidor Flask — todas as rotas e API (1800+ linhas)
├── worker.py           # Worker ThreadPool — processa fila de downloads
├── indexer.py          # Indexador SQLite in-memory — busca rápida no M3U
├── db.py               # Conexão PostgreSQL via psycopg2
├── start.sh            # Entrypoint — inicia worker + Flask
├── data/
│   ├── dados.txt       # Lista M3U (~64MB, ~560k linhas) — NUNCA commitar
│   ├── series/         # JSONs de séries para o Script Runner
│   └── filmes/         # JSONs de filmes para o Script Runner
├── templates/          # Jinja2 templates (não precisam de restart do Flask)
├── static/             # Assets estáticos
├── README.md           # Documentação técnica completa
└── SCRIPT_RUNNER.md    # Documentação do Script Runner (formato JSON, como gerar)
```

### Processos em execução
- Gerenciados via **systemd** no host de app (10.0.1.160):
  - `mdm.service` → `/opt/mdm/venv/bin/python /opt/mdm/app.py` (Flask, porta 3070)
  - `mdm-worker.service` → `/opt/mdm/venv/bin/python /opt/mdm/worker.py`
- Restart: `systemctl restart mdm mdm-worker`. NÃO use `kill` — `Restart=always` respawna.
- Templates Jinja2 são recarregados automaticamente (sem restart).

---

## Páginas e Rotas

| Página | Rota | Destino dos downloads |
|--------|------|-----------------------|
| Login | `/login` | — |
| Dashboard | `/dashboard` | — |
| Filmes | `/filmes` | `/mnt/media/filmes/` |
| Séries | `/series` | `/mnt/media/series/{nome}/Season {n}/` |
| Desenhos | `/desenhos` | `/mnt/media/desenhos/` |
| Animes | `/animes` | `/mnt/media/animes/` |
| Tendências | `/tendencias` | — (TMDB API) |
| Downloads | `/downloads` | — (fila) |
| Storage | `/storage` | — (navegar `/mnt/media`) |
| Webhooks | `/webhooks` | — |
| Updates | `/updates` | — (changelog) |
| Coringa | `/coringa` | qualquer pasta em `/mnt/media` |
| M3U | `/m3u` | — (upload dados.txt) |
| Cleanup | `/cleanup` | — (limpar arquivos incompletos) |
| Usuários | `/usuarios` | — (admin only) |
| Torrents | `/torrents` | — (qBittorrent integration) |

---

## Script Runner (Coringa / `/coringa`)

**A feature mais poderosa.** Permite enviar um JSON com lista completa de episódios de uma série para a fila de download de uma vez.

### Rota backend
`POST /run_script` — aceita `{script, dest, force}`

### Formato do JSON (série)
```json
{
  "tipo": "serie",
  "nome": "Nome da Série",
  "temporadas": [
    {
      "temporada": "1",
      "episodios": [
        { "nome": "Serie S01E01", "url": "http://..." },
        { "nome": "Serie S01E02", "url": "http://..." }
      ]
    }
  ]
}
```

### Tipos suportados
| tipo | destino |
|------|---------|
| `serie` | `/mnt/media/series` |
| `filme` | `/mnt/media/filmes` |
| `desenho` | `/mnt/media/desenhos` |
| `anime` | `/mnt/media/animes` |
| `desenho_serie` | `/mnt/media/desenhosseries` |
| `anime_serie` | `/mnt/media/animeseries` |

### JSONs de séries gerados em `/opt/mdm/data/series/`
Esses arquivos são colados diretamente no Script Runner:

| Arquivo | Série |
|---------|-------|
| `halo_serie_teste.json` | Halo (referência/teste) |
| `the_mandalorian.json` | The Mandalorian |
| `the_bear.json` | The Bear (O Urso) |
| `andor.json` | Andor (Star Wars) |
| `only_murders.json` | Only Murders in the Building |
| `shogun.json` | Shogun (Xógum) |
| `loki.json` | Loki |
| `wandavision.json` | WandaVision |
| `abbott_elementary.json` | Abbott Elementary |
| `greys_anatomy.json` | Grey's Anatomy (22 temporadas) |
| `modern_family.json` | Modern Family (11 temporadas) |
| `criminal_minds.json` | Criminal Minds (18 temporadas) |
| `dopesick.json` | Dopesick |
| `o_residente.json` | O Residente |
| `impuros.json` | Impuros |
| `the_dropout.json` | The Dropout |
| `pam_tommy.json` | Pam & Tommy |
| `o_livro_de_boba_fett.json` | O Livro de Boba Fett |
| `ahsoka.json` | Ahsoka |
| `x_men_97.json` | X-Men '97 |
| `what_if.json` | What If...? |
| `moon_knight.json` | Moon Knight (Cavaleiro da Lua) |
| `hawkeye.json` | Hawkeye (Gavião Arqueiro) |
| `falcon_winter_soldier.json` | Falcon and the Winter Soldier |
| `this_is_us.json` | This Is Us |
| `lost.json` | Lost (6 temporadas) |
| `the_walking_dead.json` | The Walking Dead (11 temporadas) |
| `american_horror_story.json` | American Horror Story |
| `how_i_met_your_mother.json` | How I Met Your Mother |
| `prison_break.json` | Prison Break |
| `demolidor.json` | Demolidor (Renascido) |
| `the_handmaids_tale.json` | The Handmaid's Tale (O Conto Da Aia) |
| `9_1_1.json` | 9-1-1 |
| `the_old_man.json` | The Old Man |

> **Séries sem JSON (não encontradas em dados.txt):** Smallworld, The Peripheral, Suits (pesquisar com nome alternativo)

---

## Download Coringa (`/coringa` → aba "Coringa Download")

Download avulso de qualquer link para qualquer pasta.

- **Single:** `{name, link, dest}` → cria `{dest}/{name}/links.txt`
- **Multi-arquivo:** `{items: [{name, link}], dest}` → cada item vira subpasta própria
- **Explorar pastas:** clique em pastas para navegar dentro de `/mnt/media`

### Bug histórico resolvido
- `dest_rel` vazio (pasta raiz) era bloqueado erroneamente → corrigido removendo `not dest_rel` da validação
- Navegação de pastas usava `let` em scope JS local → corrigido para `onclick="carregarPastas('path')"` explícito

---

## Indexer (`indexer.py`)

SQLite in-memory que carrega todo o `dados.txt` na memória para buscas em <100ms.

- Classifica por URL: `/movie/` → filme, `/series/` → série
- Classifica por `group-title` para desenhos, animes
- `search_all(q)` → retorna `{nome, link, logo, group, tipo}`
- **Auto-reindex** quando `dados.txt` é modificado
- Forçar reindex: `POST /api/reindex`

---

## Banco de Dados (PostgreSQL)

### Host
- **Atual:** `10.0.1.176` (postgres 17.9, owner `mdm`)
- **Antigo:** `10.0.1.31` (postgres 17.9) — migrado em 2026-05-15 (`pg_dump` → restore como user mdm)

### Tabelas
| Tabela | Uso |
|--------|-----|
| `jobs` | Fila de downloads — status, progresso, PID |
| `seasons` | Temporadas vinculadas a jobs de série |
| `users` | Usuários do sistema |
| `webhooks` | Webhooks de notificação (generic/whatsapp via `tipo` + `config jsonb`) |
| `webhook_logs` | Histórico de disparos de webhooks |

### Conexão
Configurada via `.env`:
```
DB_HOST=10.0.1.176
DB_PORT=5432
DB_USER=mdm
DB_PASS=...
DB_NAME=mdm
```

---

## Worker (`worker.py`)

| Constante | Padrão | Descrição |
|-----------|--------|-----------|
| `MAX_FILMES_SIMULTANEOS` | 5 | Downloads paralelos de filmes |
| `MAX_SERIES_SIMULTANEAS` | 10 | Downloads paralelos de séries |
| `MAX_RETRIES` | 3 | Tentativas por arquivo |
| `CHECK_INTERVAL` | 3s | Poll da fila |

---

## Como gerar um JSON de série do zero

```bash
python3 -c "
import re, json
NOME_BUSCA = 'Nome da Serie'  # como aparece no dados.txt
TIPO = 'serie'
results = {}
with open('/opt/mdm/data/dados.txt', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()
i = 0
while i < len(lines):
    line = lines[i].strip()
    if line.startswith('#EXTINF') and NOME_BUSCA.lower() in line.lower():
        nome = line.split(',')[-1].strip()
        url  = lines[i+1].strip() if i+1 < len(lines) else ''
        m = re.search(r'S(\d+)E(\d+)', nome, re.IGNORECASE)
        if m and url.startswith('http'):
            s, e = int(m.group(1)), int(m.group(2))
            if s not in results: results[s] = []
            results[s].append({'ep': e, 'nome': nome, 'url': url})
    i += 1
temporadas = []
for s in sorted(results):
    eps = sorted(results[s], key=lambda x: x['ep'])
    temporadas.append({'temporada': str(s), 'episodios': [{'nome': ep['nome'], 'url': ep['url']} for ep in eps]})
script = {'tipo': TIPO, 'nome': NOME_BUSCA, 'temporadas': temporadas}
print(json.dumps(script, ensure_ascii=False, indent=2))
" > /opt/mdm/data/series/nova_serie.json
```

> **Atenção:** Series têm nome em PT-BR no dados.txt. Ex: "Grey's Anatomy" → "Greys Anatomy" ou "Grey's Anatomy"; "Modern Family" → "Família Moderna"; "The Bear" → "O Urso"; "Hawkeye" → "Gavião Arqueiro".

---

## Nomes PT-BR no dados.txt (referência rápida)

| Nome original | Nome no dados.txt |
|---------------|-------------------|
| The Bear | O Urso |
| Modern Family | Família Moderna |
| Criminal Minds | Mentes Criminosas |
| Hawkeye | Gavião Arqueiro |
| Moon Knight | Cavaleiro da Lua |
| Falcon and the Winter Soldier | Falcão e o Soldado Invernal |
| The Handmaid's Tale | O Conto Da Aia |
| The Wheel of Time | A Roda do Tempo |
| Shogun | Xogum A Gloriosa Saga do Japao |
| The Marvelous Mrs. Maisel | Maravilhosa Sra. Maisel |
| Andor | Star Wars Andor / Star Wars_ Andor |
| Demolidor | Demolidor Renascido |
| X-Men '97 | X Men 97 |

---

## Webhooks

Dois tipos suportados (`webhooks.tipo`):

### `generic` (Discord/Slack/Telegram)
POST JSON `{ "content": "🎬 Filme finalizado: Nome do Filme" }` para `webhooks.url`.

### `whatsapp` (Evolution API)
`config` JSONB com `{server, instance, api_key, destinos[]}`.
Dispatcher faz `POST {server}/message/sendText/{instance}` com header `apikey` e body `{number, text}`, **uma vez por destino**.
Destinos podem ser números (`5511999999999`) ou grupos (`xxx@g.us`).

Schema:
```sql
webhooks(id, nome, url, tipo VARCHAR(20) DEFAULT 'generic', config JSONB, ativo, ...)
-- url é nullable; usado só para generic. Pra whatsapp armazena {server}/message/sendText/{instance} por conveniência.
```

Dispatcher único em `worker.py:send_webhook()` — branchea por `tipo`. Eventos: download concluído (filme/série), cancelado, falhou.

---

## Updates (`/updates`)

Changelog público pros usuários. **Todos veem; só o Claude (via SQL) cria.** Sem UI de criação.

Schema:
```sql
updates(id, title VARCHAR(200), body TEXT, created_at TIMESTAMP)
```

`body` é markdown — renderizado com marked.js (CDN). Página agrupa por dia, mais recente primeiro, primeiro card expandido por padrão.

Pra adicionar entry:
```sql
INSERT INTO updates (title, body, created_at)
VALUES ('Título curto', E'Markdown...\n- bullet 1\n- bullet 2', NOW());
```

Usa `E'...'` no Postgres pra interpretar `\n`.

---

## Storage Pools (multi-HD)

Sistema descobre automaticamente todos os diretórios que casam com `/mnt/media*` (constante `STORAGE_GLOB`). Cada um vira um pool selecionável em todas as páginas de download.

### API
`GET /api/storage_pools` → lista `[{path, label, used_pct, free_human, total_human, status}]`. Cores:
- `green` (≤59%), `orange` (60–85%), `red` (≥86%)

### Helpers (em `app.py`)
- `list_storage_roots()` — glob de `/mnt/media*`
- `resolve_storage(raw)` — valida ou cai no `DEFAULT_STORAGE` (`/mnt/media`)
- `category_base(storage, tipo)` — monta `{storage}/{subpasta}`
- `get_category_base(data, tipo)` — atalho que lê `data["storage"]` do request

### Subpastas por tipo (`CATEGORY_FOLDER`)
| tipo | folder |
|------|--------|
| serie | series |
| filme | filmes |
| desenho | desenhos |
| desenho_serie | desenhosseries |
| anime | animes |
| anime_serie | animeseries |

### Worker
`worker.py:get_categoria(caminho)` extrai a categoria pela 2ª parte do path via regex `^/mnt/[^/]+/([^/]+)`. **Storage-agnóstico** — funciona com qualquer mount.

### UI
Partial `templates/_storage_picker.html`. Inclui via `{% include "_storage_picker.html" %}`. Expõe `getSelectedStorage()` e `onStorageChange(cb)` no JS. Memoriza a escolha em `localStorage.mdm.selectedStorage`.

Já incluído em: `filmes.html`, `series.html`, `desenhos.html`, `animes.html`, `coringa.html`.

---

## Segurança e Deploy

- `.env` nunca commitar — contém credenciais do banco
- `data/dados.txt` nunca commitar — lista M3U proprietária
- Alterar `secret_key` e senha root antes de produção
- Docker: `docker compose up -d --build` | porta 3070
- Reverse proxy (Nginx/Traefik) recomendado para HTTPS

---

## Histórico de Features Principais

| Versão | Feature |
|--------|---------|
| v2.4 | Storage Pools — multi-HD com card colorido em todas as páginas de download (auto-discovery /mnt/media*) |
| v2.3 | Página `/updates` (changelog público com markdown, agrupado por dia) |
| v2.2 | Webhook WhatsApp via Evolution API (multi-destino, chips UI), edição inline de webhooks, migração DB pro host 10.0.1.176 |
| v2.1 | Desenhos, Animes, Storage Manager (criar/renomear/mover), Dashboard avançado (gráfico 7 dias, paginação), Badges TMDB inteligentes |
| v2.0 | Tendências TMDB, Download Coringa multi-arquivo, Coringa folder explorer |
| v1.x | Filmes, Séries, Script Runner, Webhooks, Indexer in-memory |

---

*Atualizado: 2026-05-15*
