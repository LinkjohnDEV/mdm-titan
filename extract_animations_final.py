import re
import json
import os
import unicodedata

INPUT_FILE = "/opt/mdm/data/dados.txt"
OUTPUT_FILE = "/opt/mdm/data/series/desenho.json"

raw_list = """Pinóquio (Guillermo del Toro) | 02. Klaus | 03. Os Caras Malvados | 04. A Família Mitchell e a Revolta das Máquinas | 05. Ron Bugado | 06. Spirit: O Corcel Indomável | 07. O Caminho para El Dorado | 08. Sinbad: A Lenda dos Sete Mares | 09. O Gigante de Ferro | 10. O Estranho Mundo de Jack | 11. A Noiva Cadáver | 12. Frankenweenie | 13. Coraline e o Mundo Secreto | 14. ParaNorman | 15. Os Boxtrolls | 16. Kubo e as Cordas Mágicas | 17. Elo Perdido | 18. O Pequeno Príncipe (2015) | 19. Minha Vida de Abobrinha | 20. A Canção do Oceano | 21. Wolfwalkers | 22. Uma Viagem ao Mundo das Fábulas | 23. O Segredo de Kells | 24. Ernest & Celestine | 25. O Ilusionista (2010) | 26. As Bicicletas de Belleville | 27. Persépolis | 28. Anomalisa | 29. Ilha dos Cachorros | 30. O Fantástico Sr. Raposo | 31. Rango | 32. As Aventuras de Tintim | 33. O Expresso Polar | 34. Beowulf | 35. Os Fantasmas de Scrooge | 36. Monster House (A Casa Monstro) | 37. A Lenda dos Guardiões | 38. Ga'Hoole | 39. Happy Feet: O Pinguim | 40. O Bicho vai Pegar | 41. Tá Dando Onda | 42. Hotel Transilvânia (Saga) | 43. Chuva de Hambúrguer (Saga) | 44. Os Smurfs: E a Vila Perdida | 45. Emoji: O Filme | 46. Angry Birds: O Filme (Saga) | 47. Capitão Cueca: O Filme | 48. Trolls (Saga) | 49. Abominável | 50. Um Pequeno Grande Plano | 51. Smallfoot (Pé Pequeno) | 52. Cegonhas: A História que não te Contaram | 53. Lego Batman: O Filme | 54. Lego Ninjago: O Filme | 55. Uma Aventura Lego (Saga) | 56. A Origem dos Guardiões | 57. As Aventuras de Peabody e Sherman | 58. Turbo | 59. Cada Um na Sua Casa | 60. As Aventuras de Tadeo | 61. Mortadelo e Salaminho | 62. O Que Será de Nozes? | 63. O Reino Escondido | 64. Epic | 65. Robôs | 66. Rio (Saga) | 67. O Touro Ferdinando | 68. Um Espião Animal | 69. Snoopy & Charlie Brown: Peanuts | 70. Garfield: Fora de Casa (2024) | 71. Scooby! O Filme | 72. Tom & Jerry (2021) | 73. Looney Tunes: De Volta à Ação | 74. Space Jam: Um Novo Legado | 75. Batman: A Máscara do Fantasma | 76. Batman Contra o Capuz Vermelho | 77. Liga da Justiça: Ponto de Ignição | 78. A Morte do Superman | 79. Batman: Piada Mortal | 80. Homem-Aranha: No Aranhaverso | 81. As Tartarugas Ninja: Caos Mutante | 82. Sing: Quem Canta Seus Males Espanta (Saga) | 83. Pets: A Vida Secreta dos Bichos (Saga) | 84. O Grinch (2018) | 85. Lorax: Em Busca da Trúfula Perdida | 86. Horton e o Mundo dos Quem | 87. Os Sem-Floresta | 88. Formiguinhaz | 89. O Príncipe do Egito | 90. Joseph: Rei dos Sonhos | 91. Spirit: O Indomável | 92. Madagascar (Saga) | 93. Megamente | 94. Bee Movie | 95. O Espanta Tubarões | 96. Wallace & Gromit: A Batalha dos Vegetais | 97. Fuga das Galinhas (Saga) | 98. Shaun, o Carneiro (Saga) | 99. Piratas Pirados! | 100. O Homem das Cavernas."""

def normalize(text):
    if not text: return ""
    text = "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    return " ".join(text.split())

def clean_list(raw):
    cleaned = re.sub(r'\d+\.\s*', '', raw)
    titles = [t.strip() for t in cleaned.split('|')]
    return titles

titles = clean_list(raw_list)
normalized_titles = {title: normalize(title) for title in titles}
best_matches = {title: None for title in titles}

# Mapeamento manual para casos difíceis
manual_subs = {
    "Pinóquio (Guillermo del Toro)": "Pinoquio Guillermo del Toro",
    "Ga'Hoole": "Gahoole",
    "Snoopy & Charlie Brown: Peanuts": "Snoopy e Charlie Brown",
    "Formiguinhaz": "FormiguinhaZ",
}

def get_score(name, original_title):
    score = 0
    name_upper = name.upper()
    # Bloqueia 4K
    if any(x in name_upper for x in ["4K", "UHD", "2160P"]): return -1000
    # Prefere Dublado
    if "DUB" in name_upper: score += 100
    
    norm_name = normalize(name)
    norm_orig = normalize(original_title)
    
    if norm_name == norm_orig: score += 300
    elif norm_name.startswith(norm_orig): score += 250
    elif norm_orig in norm_name: score += 200
    
    # Check manual substitutions
    if original_title in manual_subs:
        norm_sub = normalize(manual_subs[original_title])
        if norm_sub in norm_name: score += 180
        
    return score

print(f"Lendo {INPUT_FILE}...")
with open(INPUT_FILE, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

print("Processando filmes...")
i = 0
while i < len(lines):
    line = lines[i].strip()
    if line.startswith("#EXTINF"):
        url = lines[i+1].strip() if i + 1 < len(lines) else ""
        display_name = line.split(",")[-1].strip() if "," in line else ""
        norm_display = normalize(display_name)
        
        for title in titles:
            targets = [normalized_titles[title]]
            if title in manual_subs: targets.append(normalize(manual_subs[title]))
            if ":" in title: targets.append(normalize(title.split(":")[0]))
            if "(" in title: targets.append(normalize(title.split("(")[0]))
            
            match_found = False
            for target in targets:
                if not target: continue
                if len(target) < 4:
                    if re.search(r'\b' + re.escape(target) + r'\b', norm_display):
                        match_found = True
                        break
                else:
                    if target in norm_display:
                        match_found = True
                        break
            
            if match_found:
                score = get_score(display_name, title)
                if score >= 0:
                    current_best = best_matches[title]
                    if current_best is None or score > current_best['score']:
                        best_matches[title] = {"nome": display_name, "url": url, "score": score}
        i += 2
    else: i += 1

filmes_list = []
not_found = 0
for title in titles:
    match = best_matches[title]
    if match:
        filmes_list.append({"nome": match['nome'], "url": match['url']})
    else:
        not_found += 1
        filmes_list.append({"nome": f"{title} (Não Encontrado)", "url": ""})

data = {
    "tipo": "filmes_batch",
    "filmes": filmes_list
}

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Salvo em {OUTPUT_FILE}")
print(f"Não encontrados: {not_found}")
