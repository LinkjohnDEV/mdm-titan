# MDM Titan — Script Runner

> Use a aba **Script Runner** em `/coringa` para executar scripts em lote.  
> Cole o JSON, escolha a pasta de destino e clique **Executar Script**.

---

## Tipos disponíveis

| `tipo`          | Destino padrão              | Descrição              |
|-----------------|-----------------------------|------------------------|
| `serie`         | `/mnt/media/series`         | Séries normais         |
| `filme`         | `/mnt/media/filmes`         | Filmes normais         |
| `desenho`       | `/mnt/media/desenhos`       | Filme animado          |
| `anime`         | `/mnt/media/animes`         | Filme anime            |
| `desenho_serie` | `/mnt/media/desenhosseries` | Série animada          |
| `anime_serie`   | `/mnt/media/animeseries`    | Série anime            |

> A pasta de destino pode ser alterada no **browser de pastas** que aparece após o preview.

---

## Formato: Série com temporadas

```json
{
  "tipo": "serie",
  "nome": "Nome da Série",
  "temporadas": [
    {
      "temporada": "1",
      "episodios": [
        { "nome": "S01E01 - Título", "url": "http://link.mp4" },
        { "nome": "S01E02 - Título", "url": "http://link.mp4" }
      ]
    },
    {
      "temporada": "2",
      "episodios": [
        { "nome": "S02E01 - Título", "url": "http://link.mp4" }
      ]
    }
  ]
}
```

Funciona para: `serie`, `desenho_serie`, `anime_serie`

---

## Formato: Filme (arquivo único)

```json
{
  "tipo": "filme",
  "nome": "Nome do Filme",
  "url": "http://link-direto.mp4"
}
```

Funciona para: `filme`, `desenho`, `anime`

---

## Como gerar o JSON via terminal

Para buscar uma série específica no `dados.txt` e gerar o script:

```bash
python3 -c "
import re, json

NOME_BUSCA = 'Grimm'   # <-- altere aqui
TIPO = 'serie'         # <-- altere aqui

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
"
```

Salvar em arquivo:
```bash
python3 script_acima.py > minha_serie.json
```

---

## Estrutura criada no disco

Para séries, a estrutura de pastas criada é:

```
/mnt/media/series/
  └── Nome da Série/
        ├── Season 1/
        │     ├── links.txt   ← episódios da T1
        └── Season 2/
              └── links.txt   ← episódios da T2
```

Cada `links.txt` contém uma linha por episódio:
```
S01E01 - Título|http://url.mp4
S01E02 - Título|http://url.mp4
```

---

## Rota backend

`POST /run_script`

```json
{
  "script": { ... },
  "dest": "series/subpasta",
  "force": false
}
```

- `dest` — caminho relativo dentro de `/mnt/media` (opcional, usa padrão do tipo)
- `force` — `true` para sobrescrever jobs existentes
