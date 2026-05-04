import re
import json
import os
import unicodedata
import time

INPUT_FILE = "/opt/mdm/data/dados.txt"
OUTPUT_FILE = "/opt/mdm/data/series/animes.json"

raw_list = """A Viagem de Chihiro | 102. Meu Amigo Totoro | 103. O Castelo Animado | 104. Princesa Mononoke | 105. O Túmulo dos Vagalumes | 106. Ponyo: Uma Amizade que Veio do Mar | 107. O Serviço de Entregas da Kiki | 108. O Castelo no Céu | 109. Nausicaä do Vale do Vento | 110. Vidas ao Vento | 111. O Menino e a Garça | 112. O Conto da Princesa Kaguya | 113. O Reino dos Gatos | 114. O Mundo dos Pequeninos | 115. As Memórias de Marnie | 116. Your Name (Kimi no Na wa) | 117. Weathering with You (O Tempo com Você) | 118. Suzume | 119. O Jardim das Palavras | 120. 5 Centímetros por Segundo | 121. O Lugar Prometido em Nossa Juventude | 122. A Voz do Silêncio (Koe no Katachi) | 123. Quero Comer Seu Pâncreas | 124. Ride Your Wave: Juntos no Mar | 125. Lu Over the Wall | 126. Night is Short, Walk on Girl | 127. Mind Game | 128. Tekkonkinkreet | 129. Paprika | 130. Perfect Blue | 131. Atriz Milenar | 132. Padrinhos de Tóquio | 133. Ghost in the Shell (O Fantasma do Futuro) | 134. Akira | 135. Metropolis | 136. Steamboy | 137. Cowboy Bebop: O Filme | 138. Trigun: Badlands Rumble | 139. Blood: The Last Vampire | 140. Vampire Hunter D: Bloodlust | 141. Ninja Scroll | 142. Sword of the Stranger | 143. The Boy and the Beast | 144. Wolf Children (Crianças Lobo) | 145. Summer Wars | 146. A Garota que Conquistou o Tempo | 147. Belle | 148. Mirai | 149. Promare | 150. Redline | 151. Saga Dragon Ball Super: Broly | 152. Saga Dragon Ball Super: Super Hero | 153. Saga One Piece Film: Red | 154. Saga One Piece: Stampede | 155. Saga One Piece: Z | 156. Saga Naruto: The Last | 157. Saga Boruto: Naruto the Movie | 158. Saga Demon Slayer: Mugen Train | 159. Saga Jujutsu Kaisen 0 | 160. Saga Evangelion: 1.11, 2.22, 3.33 e 3.0+1.0 | 161. Blame! (Netflix) | 162. Godzilla: Planeta dos Monstros (Trilogia) | 163. Gantz:O | 164. Saint Seiya: Lenda do Santuário | 165. Harlock: Space Pirate | 166. Appleseed: Alpha | 167. Final Fantasy VII: Advent Children | 168. Final Fantasy XV: Kingsglaive | 169. The Sky Crawlers | 170. Jin-Roh: The Wolf Brigade | 171. Colorful | 172. Giovanni's Island | 173. In This Corner of the World | 174. The Anthem of the Heart | 175. Maquia: When the Promised Flower Blooms | 176. A Whisker Away (Olhos de Gato) | 177. Bubble (Netflix) | 178. Drifting Home | 179. Flavors of Youth | 180. Children of the Sea | 181. Words Bubble Up Like Soda Pop | 182. Goodbye, Don Glees! | 183. The First Slam Dunk | 184. Blue Giant | 185. Lonely Castle in the Mirror | 186. Pompo: The Cinephile | 187. Inu-Oh | 188. The Deer King | 189. The Tunnel to Summer, the Exit of Goodbyes | 190. Maboroshi | 191. Sand Land | 192. Look Back (2024) | 193. Black Clover: Sword of the Wizard King | 194. The Seven Deadly Sins: Cursed by Light | 195. Mobile Suit Gundam: Hathaway | 196. Psycho-Pass Providence | 197. Rascal Does Not Dream of a Dreaming Girl | 198. Fate/stay night: Heaven's Feel (Trilogia) | 199. Made in Abyss: Dawn of the Deep Soul | 200. The Quintessential Quintuplets Movie."""

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
# Preparar targets de busca (incluindo variações)
search_targets = {}
for title in titles:
    t_targets = [normalize(title)]
    if title.startswith("Saga "): t_targets.append(normalize(title[5:]))
    if ":" in title: t_targets.append(normalize(title.split(":")[0]))
    if "(" in title: t_targets.append(normalize(title.split("(")[0]))
    # Mapeamentos específicos inline para velocidade
    if "Chihiro" in title: t_targets.append("viagem de chihiro")
    if "Totoro" in title: t_targets.append("meu amigo totoro")
    if "Kimi no Na wa" in title: t_targets.append("your name")
    if "Koe no Katachi" in title: t_targets.append("voz do silencio")
    
    search_targets[title] = [t for t in t_targets if len(t) > 2]

best_matches = {title: None for title in titles}

def get_score(name, original_title):
    score = 0
    name_upper = name.upper()
    if any(x in name_upper for x in ["4K", "UHD", "2160P"]): return -1000
    if "DUB" in name_upper: score += 100
    
    norm_name = normalize(name)
    norm_orig = normalize(original_title)
    
    if norm_name == norm_orig: score += 300
    elif norm_name.startswith(norm_orig): score += 250
    elif norm_orig in norm_name: score += 200
    return score

print(f"Lendo {INPUT_FILE}...")
with open(INPUT_FILE, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

total_lines = len(lines)
print(f"Processando {total_lines} linhas para {len(titles)} animes...")

start_time = time.time()
for i in range(0, total_lines, 2):
    line = lines[i].strip()
    if line.startswith("#EXTINF"):
        if i % 50000 == 0:
            print(f"Progresso: {i/total_lines*100:.1f}%")
            
        display_name = line.split(",")[-1].strip() if "," in line else ""
        if not display_name: continue
        
        norm_display = normalize(display_name)
        url = lines[i+1].strip() if i + 1 < total_lines else ""
        
        for title, targets in search_targets.items():
            match_found = False
            for target in targets:
                if target in norm_display:
                    match_found = True
                    break
            
            if match_found:
                score = get_score(display_name, title)
                if score >= 0:
                    current_best = best_matches[title]
                    if current_best is None or score > current_best['score']:
                        best_matches[title] = {"nome": display_name, "url": url, "score": score}

filmes_list = []
not_found = 0
for title in titles:
    match = best_matches[title]
    if match:
        filmes_list.append({"nome": match['nome'], "url": match['url']})
    else:
        not_found += 1
        filmes_list.append({"nome": f"{title} (Não Encontrado)", "url": ""})

data = {"tipo": "filmes_batch", "filmes": filmes_list}
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

end_time = time.time()
print(f"Salvo em {OUTPUT_FILE} em {end_time - start_time:.1f}s")
print(f"Não encontrados: {not_found}")
