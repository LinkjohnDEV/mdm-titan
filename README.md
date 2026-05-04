<p align="center">
  <img src="https://img.shields.io/badge/MDM-TITAN-blue?style=for-the-badge&logo=docker&logoColor=white" alt="MDM Titan">
  <img src="https://img.shields.io/badge/Python-3.12-yellow?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Flask-3.1-green?style=for-the-badge&logo=flask&logoColor=white" alt="Flask">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker">
</p>

# 🛡️ MDM Titan

**Media Download Manager** — Sistema completo de automação para download e gerenciamento de mídia (filmes e séries) a partir de listas M3U/IPTV, com interface web moderna, fila inteligente e notificações via webhook.

---

## ✨ Features

| Feature | Descrição |
|---------|-----------|
| 🎬 **Download de Filmes** | Busca e baixa filmes diretamente de listas M3U |
| 📺 **Download de Séries** | Busca episódios, seleciona e envia para fila automatizada |
| 🎨 **Download de Desenhos** | Busca e baixa filmes e séries animadas — salva em `/mnt/media/desenhos/` |
| ⛩️ **Download de Animes** | Busca e baixa filmes e séries de anime — salva em `/mnt/media/animes/` |
| ⚡ **Downloads Simultâneos** | Até **5 filmes** + **10 séries** ao mesmo tempo |
| 🔍 **Busca Instantânea** | Indexação SQLite in-memory — busca em <100ms em arquivos 100MB+ |
| 🔄 **Auto-Reindex** | Detecta automaticamente quando o `dados.txt` é atualizado |
| ❌ **Cancelamento em Tempo Real** | Para downloads em andamento em <5 segundos |
| 📊 **Dashboard Avançado** | Métricas de disco, uptime, jobs ativos, gráfico 7 dias, atividade recente com paginação |
| 🗂️ **Storage Manager** | Navegação + criação de pastas + renomear + mover arquivos |
| 🔔 **Webhooks** | Notificações automáticas (Discord, etc.) em cada evento |
| 🎭 **Tendências Inteligentes** | Badges TMDB: Anime 🔴, Série Animada 🩷, Desenho 🟢, Série 🟣, Filme 🔵 |
| 🐳 **Docker Ready** | Deploy com um único comando via Docker Compose |

---

## 📸 Interface

A interface usa design dark premium com **TailwindCSS**, **Lucide Icons** e tipografia **Plus Jakarta Sans**.

### Páginas

| Página | Rota | Descrição |
|--------|------|-----------|
| Login | `/login` | Autenticação do sistema |
| Dashboard | `/dashboard` | Métricas gerais, disco, uptime, gráfico 7 dias, downloads ativos, atividade recente |
| Filmes | `/filmes` | Busca e adiciona filmes à fila → `/mnt/media/filmes/` |
| Séries | `/series` | Busca episódios e monta temporadas → `/mnt/media/series/Season X/` |
| Desenhos | `/desenhos` | Modo Filme Animado + Modo Série Animada → `/mnt/media/desenhos/` |
| Animes | `/animes` | Modo Filme Anime + Modo Série Anime → `/mnt/media/animes/` |
| Tendências | `/tendencias` | Trending + Populares TMDB com badges inteligentes por tipo |
| Downloads | `/downloads` | Fila de downloads com progresso em tempo real |
| Storage | `/storage` | Navegador com criação de pastas, renomear e mover arquivos |
| Webhooks | `/webhooks` | Gerenciamento de webhooks |

---

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────────┐
│                  Docker Container                │
│                                                  │
│   ┌──────────┐     ┌───────────┐                │
│   │  Flask    │     │  Worker   │                │
│   │  (Web UI) │     │  (Pool)   │                │
│   │  :3070    │     │           │                │
│   └────┬─────┘     └─────┬─────┘                │
│        │                  │                      │
│   ┌────┴──────────────────┴─────┐                │
│   │      PostgreSQL (externo)    │               │
│   │    jobs | webhooks | users   │               │
│   └──────────────────────────────┘               │
│                                                  │
│   ┌──────────┐  ┌────────────────┐              │
│   │ Indexer   │  │  /mnt/media    │              │
│   │ (SQLite)  │  │  (storage)     │              │
│   └──────────┘  └────────────────┘              │
└─────────────────────────────────────────────────┘
```

### Componentes

| Arquivo | Função |
|---------|--------|
| `app.py` | Servidor Flask — rotas, API, interface web |
| `worker.py` | Worker com ThreadPool — processa fila de downloads |
| `indexer.py` | Indexador SQLite in-memory — busca rápida no M3U |
| `db.py` | Conexão PostgreSQL |
| `start.sh` | Entrypoint Docker — inicia worker + Flask |

---

## 🚀 Instalação

### Pré-requisitos

- Docker + Docker Compose
- PostgreSQL 17+ (externo)
- Volume de storage montado em `/mnt/media`

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/mdm-titan.git
cd mdm-titan
```

### 2. Configure o `.env`

```env
DB_HOST=172.172.1.88
DB_PORT=2437
DB_USER=mdm
DB_PASS=SenhaForteAqui123!
DB_NAME=mdm
```

### 3. Crie o banco de dados

Crie o banco e rode o script `postgres_schema.sql` (ou crie as tabelas abaixo):

```sql
CREATE DATABASE mdm;

-- Conecte no banco mdm e execute:

CREATE TABLE IF NOT EXISTS jobs (
  id SERIAL PRIMARY KEY,
  series_name VARCHAR(255) DEFAULT NULL,
  status VARCHAR(20) DEFAULT 'queued',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP DEFAULT NULL,
  tipo VARCHAR(20) DEFAULT 'serie',
  temporada VARCHAR(20) DEFAULT NULL,
  caminho VARCHAR(255) DEFAULT NULL,
  sobrescrever BOOLEAN DEFAULT FALSE,
  pid INTEGER DEFAULT NULL,
  episodio VARCHAR(50) DEFAULT NULL,
  total_episodios INTEGER DEFAULT 0,
  episodios_baixados INTEGER DEFAULT 0,
  retries INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS seasons (
  id SERIAL PRIMARY KEY,
  job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
  season_number INTEGER DEFAULT NULL,
  status VARCHAR(20) DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  username VARCHAR(50) UNIQUE,
  password VARCHAR(255) DEFAULT NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'admin',
  ativo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS webhooks (
  id SERIAL PRIMARY KEY,
  nome VARCHAR(100) NOT NULL,
  url TEXT NOT NULL,
  ativo BOOLEAN DEFAULT TRUE,
  ultima_execucao TIMESTAMP DEFAULT NULL,
  ultimo_status VARCHAR(20) DEFAULT NULL,
  ultimo_codigo INTEGER DEFAULT NULL,
  criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS webhook_logs (
  id SERIAL PRIMARY KEY,
  webhook_id INTEGER REFERENCES webhooks(id) ON DELETE CASCADE,
  status VARCHAR(20) DEFAULT NULL,
  codigo INTEGER DEFAULT NULL,
  enviado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 4. Coloque sua lista M3U

```bash
# Copie seu arquivo M3U/lista IPTV para:
cp sua_lista.txt data/dados.txt
```

O sistema aceita listas no formato M3U padrão:
```
#EXTM3U
#EXTINF:-1 tvg-name="Nome do Filme" group-title="FILMES",Nome do Filme
http://servidor.com/movie/user/pass/12345.mp4
#EXTINF:-1 tvg-name="Serie S01E01" group-title="SÉRIES",Serie S01E01
http://servidor.com/series/user/pass/67890.mp4
```

### 5. Deploy

```bash
docker compose up -d --build
```

### 6. Acesse

```
http://seu-ip:3070
```

**Login padrão:** `root` / `SenhaDefinidaEmApp`

> ⚠️ **Importante**: Altere a senha em `app.py` antes do deploy em produção.

---

## 📂 Estrutura do Projeto

```
mdm-titan/
├── app.py                 # Servidor Flask — rotas, API, auth
├── worker.py              # Worker com ThreadPool — processa fila de downloads
├── indexer.py             # Indexador SQLite in-memory — busca rápida no M3U
├── db.py                  # Conexão PostgreSQL
├── start.sh               # Entrypoint Docker — inicia worker + Flask
├── requirements.txt       # Dependências Python
├── Dockerfile             # Build da imagem
├── docker-compose.yml     # Orquestração
├── .env                   # Variáveis de ambiente (não commitar!)
├── data/
│   └── dados.txt          # Lista M3U (seu arquivo IPTV)
├── logs/                  # Logs do sistema
├── templates/
│   ├── base.html          # Layout base (sidebar + header)
│   ├── login.html         # Tela de login
│   ├── dashboard.html     # Dashboard avançado com gráficos e paginação
│   ├── filmes.html        # Busca e download de filmes
│   ├── series.html        # Busca e download de séries
│   ├── desenhos.html      # Busca e download de desenhos (filme + série)
│   ├── animes.html        # Busca e download de animes (filme + série)
│   ├── tendencias.html    # Trending + Populares TMDB com badges inteligentes
│   ├── downloads.html     # Fila de downloads com progresso em tempo real
│   ├── storage.html       # Storage manager (navegar, criar, renomear, mover)
│   └── webhooks.html      # Gerenciamento de webhooks
└── static/                # Assets estáticos
```

---

## ⚙️ Configurações do Worker

Edite as constantes no topo de `worker.py`:

| Constante | Padrão | Descrição |
|-----------|--------|-----------|
| `MAX_FILMES_SIMULTANEOS` | 5 | Downloads de filmes em paralelo |
| `MAX_SERIES_SIMULTANEAS` | 10 | Downloads de séries em paralelo |
| `MAX_RETRIES` | 3 | Tentativas por arquivo |
| `CHECK_INTERVAL` | 3s | Intervalo de verificação da fila |
| `CANCEL_CHECK_BYTES` | 1MB | Intervalo de verificação de cancelamento |

---

## 🔌 API Endpoints

### Downloads
| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/api/jobs` | Lista todos os jobs |
| GET | `/api/progress/<id>` | Progresso de um job |
| POST | `/api/cancel/<id>` | Cancela um job |

### Busca
| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/search_filme?q=termo` | Busca filmes |
| GET | `/buscar_series?q=termo` | Busca séries |
| GET | `/search_desenho_filme?q=termo` | Busca filmes de animação/desenho |
| GET | `/search_desenho_serie?q=termo` | Busca séries de animação/desenho |
| GET | `/search_anime_filme?q=termo` | Busca filmes de anime |
| GET | `/search_anime_serie?q=termo` | Busca séries de anime |
| POST | `/api/reindex` | Força re-indexação do dados.txt |
| GET | `/api/index_stats` | Estatísticas do índice |

### Mídia
| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/add_filme` | Adiciona filme à fila → `/mnt/media/filmes/` |
| POST | `/add_serie` | Adiciona temporada à fila → `/mnt/media/series/` |
| POST | `/add_desenho_filme` | Adiciona filme animado → `/mnt/media/desenhos/` |
| POST | `/add_desenho_serie` | Adiciona série animada → `/mnt/media/desenhos/` |
| POST | `/add_anime_filme` | Adiciona filme anime → `/mnt/media/animes/` |
| POST | `/add_anime_serie` | Adiciona série anime → `/mnt/media/animes/` |

### Storage
| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/api/storage/mkdir` | Cria nova pasta |
| POST | `/api/storage/rename` | Renomeia arquivo ou pasta |
| POST | `/api/storage/move` | Move arquivo ou pasta |

### Webhooks
| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/api/webhooks` | Lista webhooks |
| POST | `/api/webhooks` | Cria webhook |
| POST | `/api/webhooks/<id>/toggle` | Ativa/desativa |
| POST | `/api/webhooks/<id>/delete` | Remove |
| POST | `/api/webhooks/<id>/test` | Testa webhook |

### Sistema
| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/api/dashboard_stats` | Métricas do dashboard |

---

## 🔔 Webhooks

Configure webhooks para receber notificações automáticas. Compatível com **Discord**, **Slack**, **Telegram bots** e qualquer endpoint que aceite POST JSON.

### Formato da mensagem

```json
{
  "content": "🎬 Filme finalizado: Avatar O Caminho Da Agua"
}
```

### Eventos notificados

| Evento | Emoji | Mensagem |
|--------|-------|----------|
| Download concluído (filme) | 🎬 | `Filme finalizado: {nome}` |
| Download concluído (série) | ✅ | `Download finalizado: {nome} - Temporada {n}` |
| Download cancelado | 🛑 | `Download cancelado: {nome}` |
| Download falhou | ❌ | `Download falhou: {nome}` |

---

## 🔄 Atualizando a Lista M3U

O sistema detecta automaticamente quando o `dados.txt` é modificado. Basta:

1. Substituir o arquivo `data/dados.txt` com a nova lista
2. A próxima busca já usará os dados atualizados
3. Ou force a re-indexação: `POST /api/reindex`

> 💡 **Dica**: O indexador classifica automaticamente os itens pela URL (`/movie/` = filme, `/series/` = série).

---

## 🐳 Docker

### Volumes

| Volume | Container | Descrição |
|--------|-----------|-----------|
| `./data` | `/app/data` | Lista M3U (`dados.txt`) |
| `./logs` | `/app/logs` | Logs do sistema |
| `/mnt/media` | `/mnt/media` | Storage dos downloads |

### Portas

| Porta | Serviço |
|-------|---------|
| 3070 | Flask (Web UI + API) |

### Comandos úteis

```bash
# Build e start
docker compose up -d --build

# Ver logs
docker logs -f mdm-titan

# Restart
docker compose restart

# Stop
docker compose down

# Rebuild completo
docker compose down && docker compose up -d --build
```

---

## 📋 Dependências

```
flask==3.1.0
psycopg2-binary==2.9.9
python-dotenv==1.1.0
requests==2.32.3
```

Instalados automaticamente no Docker. Adicionalmente, o container inclui:
- **yt-dlp** (fallback para downloads)
- **ffmpeg** (processamento de mídia)

---

## 🛡️ Segurança

> ⚠️ **Antes de publicar no GitHub:**

1. **Nunca commite o `.env`** — adicione ao `.gitignore`
2. **Altere a senha padrão** em `app.py` (linha do login)
3. **Altere o `secret_key`** do Flask em `app.py`
4. **Use HTTPS** em produção (via reverse proxy como Nginx/Traefik)

### `.gitignore` recomendado

```gitignore
.env
data/dados.txt
logs/
__pycache__/
*.pyc
```

---

## 📋 Changelog

### v2.1 — Março 2026
#### Novas Funcionalidades
- **Desenhos** (`/desenhos`): nova página com abas "Filme Animado" e "Série Animada". Busca filtrada por `group-title` de animação/kids no índice M3U. Salva em `/mnt/media/desenhos/`
- **Animes** (`/animes`): nova página com abas "Filme Anime" e "Série Anime". Busca filtrada por `group-title` de anime/crunchyroll. Salva em `/mnt/media/animes/`
- **Storage Manager**: o Storage agora permite criar pastas, renomear e mover arquivos/pastas, além da navegação existente. Todas as operações têm validação server-side para não sair de `/mnt/media`
- **Dashboard avançado**: adicionados cards de downloads ativos/fila, gráfico de barras com dados reais dos últimos 7 dias, painel "Baixando Agora", tabela de atividade recente, estatísticas do índice M3U e paginação em ambas as listas (5 itens/página)
- **Correção disco**: substituído `shutil.disk_usage` por `df -B1` para compatibilidade com Proxmox/ZFS/NFS

#### Tendências — Badges Inteligentes
Os cards de Tendências e Populares agora exibem o tipo real do conteúdo detectado via metadados TMDB (`genre_ids`, `origin_country`, `original_language`):

| Badge | Cor | Critério |
|-------|-----|----------|
| 🔴 ANIME | Vermelho | Animação + origem JP ou língua `ja` |
| 🩷 SÉRIE ANIMADA | Rosa | TV + Animação, não japonês (ex: donghua) |
| 🟣 SÉRIE | Roxo | TV sem animação |
| 🟢 DESENHO | Verde | Filme + Animação ou Family/Kids |
| 🔵 FILME | Azul | Filme padrão |

#### Outras Correções
- Pastas de temporada criadas como `Season X` (era `Temporada X`) para compatibilidade com Sonarr/Jellyfin
- `indexer.py`: adicionadas 4 novas funções de busca segmentada (`search_desenhos_filmes`, `search_desenhos_series`, `search_animes_filmes`, `search_animes_series`)

---

## 📝 Licença

Este projeto é de uso pessoal. Distribua conforme necessário.

---

<p align="center">
  <b>MDM Titan</b> — Automação de mídia simplificada 🚀
</p>
