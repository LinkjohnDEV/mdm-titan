import re
import json
import os
import unicodedata

INPUT_FILE = "/opt/mdm/data/dados.txt"

list1_raw = "Caverna do Dragão | 02. Thundercats | 03. He-Man e os Mestres do Universo | 04. She-Ra: A Princesa do Poder | 05. Os Flintstones | 06. Os Jetsons | 07. Scooby-Doo, Cadê Você? | 08. Tom e Jerry | 09. Looney Tunes | 10. Pica-Pau | 11. Popeye | 12. A Pantera Cor-de-Rosa | 13. Inspetor Bugiganga | 14. Tartarugas Ninja (1987) | 15. O Inspetor | 16. Manda-Chuva | 17. Zé Colmeia | 18. Dom Pixote | 19. Pepe Legal | 20. Corrida Maluca | 21. Johnny Quest | 22. Space Ghost | 23. Birdman | 24. Super Amigos | 25. Smurfs | 26. Os Ursinhos Gummi | 27. Ducktales: Os Caçadores de Aventuras | 28. Tico e Teco e os Defensores da Lei | 29. Darkwing Duck | 30. Ursinhos Carinhosos | 31. Doug | 32. Rugrats: Os Anjinhos | 33. Hey Arnold! | 34. A Vida Moderna de Rocko | 35. Ren & Stimpy | 36. O Laboratório de Dexter | 37. As Meninas Superpoderosas | 38. A Vaca e o Frango | 39. Eu Sou o Máximo | 40. Johnny Bravo | 41. Coragem, o Cão Covarde | 42. Ed, Edd n Eddy | 43. Mansão Foster para Amigos Imaginários | 44. As Terríveis Aventuras de Billy e Mandy | 45. KND: A Turma do Bairro | 46. Apenas um Show | 47. O Incrível Mundo de Gumball | 48. Steven Universo | 49. Hora de Aventura | 50. Gravity Falls: Um Verão de Mistérios | 51. Phineas e Ferb | 52. Kim Possible | 53. Danny Phantom | 54. Os Jovens Titãs (Original) | 55. Liga da Justiça Sem Limites | 56. Batman: A Série Animada | 57. Superman: A Série Animada | 58. X-Men: Evolution | 59. Homem-Aranha (1994) | 60. Super Choque | 61. Pinky e o Cérebro | 62. Animaniacs | 63. Tiny Toon | 64. Freakazoid! | 65. Samurai Jack | 66. Star Wars: The Clone Wars | 67. Star Wars Rebels | 68. Invencível (Invincible) | 69. Primal (Genndy Tartakovsky) | 70. Arcane | 71. Castlevania | 72. The Legend of Vox Machina | 73. Cyberpunk: Mercenários | 74. Bluey | 75. Peppa Pig | 76. Patrulha Canina | 77. Mundo Bita | 78. Galinha Pintadinha | 79. Show da Luna | 80. Miraculous: As Aventuras de Ladybug | 81. Hilda | 82. She-Ra e as Princesas do Poder (Netflix) | 83. Kipo e os Animonstros | 84. O Príncipe Dragão | 85. Caçadores de Trolls: Contos de Arcadia | 86. Justiça Jovem | 87. South Park | 88. Family Guy (Uma Família da Pesada) | 89. American Dad | 90. The Cleveland Show | 91. King of the Hill (O Rei do Pedaço) | 92. Bob's Burgers | 93. BoJack Horseman | 94. Big Mouth | 95. F is for Family | 96. Disenchantment ((Des)encanto) | 97. Final Space | 98. Solar Opposites | 99. Love, Death & Robots | 100. Smiling Friends"
list2_raw = "Os Cavaleiros do Zodíaco | 102. Yu Yu Hakusho | 103. Hunter x Hunter | 104. Fullmetal Alchemist: Brotherhood | 105. Bleach | 106. Death Note | 107. Attack on Titan (Shingeki no Kyojin) | 108. My Hero Academia | 109. Jujutsu Kaisen | 110. Demon Slayer (Kimetsu no Yaiba) | 111. One Punch Man | 112. Cowboy Bebop | 113. Evangelion | 114. Samurai Champloo | 115. Hellsing Ultimate | 116. Black Clover | 117. Fairy Tail | 118. The Seven Deadly Sins | 119. Tokyo Ghoul | 120. Blue Lock | 121. Chainsaw Man | 122. Spy x Family | 123. Solo Leveling | 124. Vinland Saga | 125. Mob Psycho 100 | 126. Haikyu!! | 127. Slam Dunk | 128. Captain Tsubasa (Super Campeões) | 129. InuYasha | 130. Rurouni Kenshin (Samurai X) | 131. Yu-Gi-Oh! | 132. Digimon Adventure | 133. Pokémon (Saga Original) | 134. Beyblade | 135. Medabots | 136. Bakugan | 137. Sailor Moon | 138. Cardcaptor Sakura | 139. Guerreiras Mágicas de Rayearth | 140. Shaman King | 141. JoJo's Bizarre Adventure | 142. Berserk | 143. Ghost in the Shell | 144. Akira | 145. Steins;Gate | 146. Code Geass | 147. Sword Art Online | 148. Re:Zero | 149. Dr. Stone | 150. The Rising of the Shield Hero | 151. That Time I Got Reincarnated as a Slime | 152. Mashle | 153. Hell's Paradise | 154. Ranking of Kings | 155. Dorohedoro | 156. Parasyte: The Maxim | 157. Devilman Crybaby | 158. Aggretsuko | 159. Beastars | 160. Great Pretender | 161. Drawn Together (Casa dos Animados) | 162. Celebrity Deathmatch | 163. Beavis and Butt-Head | 164. Daria | 165. Æon Flux | 166. Spawn: The Animated Series | 167. Todd McFarlane's Spawn | 168. The Maxx | 169. Duckman | 170. Happy Tree Friends | 171. Robot Chicken (Frango Robô) | 172. Aqua Teen Hunger Force | 173. Harvey Birdman, Attorney at Law | 174. The Venture Bros. | 175. Archer | 176. Harley Quinn (Série Animada) | 177. Over the Garden Wall (O Segredo Além do Jardim) | 178. Infinity Train | 179. The Owl House (A Casa Coruja) | 180. Amphibia | 181. SpongeBob SquarePants (Bob Esponja) | 182. The Fairly OddParents (Padrinhos Mágicos) | 183. Jimmy Neutron: Menino Gênio | 184. Invader Zim | 185. My Life as a Teenage Robot (Uma Robô Adolescente) | 186. The Loud House | 187. CatDog | 188. Aaahh!!! Real Monsters | 189. Angry Beavers (Os Castores Pirados) | 190. Wild Thornberrys (Os Thornberrys) | 191. Rocket Power | 192. As Aventuras de Jackie Chan | 193. X-Men '97 | 194. Avengers: Earth's Mightiest Heroes | 195. Wolverine and the X-Men | 196. Spectacular Spider-Man | 197. Transformers: Prime | 198. He-Man and the Masters of the Universe (2002) | 199. Voltron: Legendary Defender | 200. Mestres do Universo: Salvando Eternia"

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

titles1 = clean_list(list1_raw)
titles2 = clean_list(list2_raw)
all_titles = titles1 + titles2

# Mapping for common differences
manual_subs = {
    "She-Ra: A Princesa do Poder": "She-Ra",
    "Tartarugas Ninja (1987)": "Tartarugas Ninja",
    "Johnny Quest": "Jonny Quest",
    "Super Amigos": "Superamigos",
    "Family Guy (Uma Família da Pesada)": "Uma Familia da Pesada",
    "King of the Hill (O Rei do Pedaço)": "O Rei do Pedaco",
    "Family Guy": "Uma Familia da Pesada",
    "Captain Tsubasa (Super Campeões)": "Super Campeoes",
    "Rurouni Kenshin (Samurai X)": "Samurai X",
    "SpongeBob SquarePants (Bob Esponja)": "Bob Esponja",
    "The Fairly OddParents (Padrinhos Mágicos)": "Padrinhos Magicos",
    "Drawn Together (Casa dos Animados)": "Casa dos Animados",
    "Happy Tree Friends": "Happy Tree",
}

best_matches = {title: None for title in all_titles}

def get_score(name, original_title):
    score = 0
    name_upper = name.upper()
    if any(x in name_upper for x in ["4K", "UHD", "2160P"]): return -1000
    if "DUB" in name_upper: score += 100
    if "24H" in name_upper: score += 50
    if re.search(r'S0?1E0?1\b', name_upper) or re.search(r'0?1X0?1\b', name_upper): score += 40
    elif "S01" in name_upper: score += 20
    
    norm_name = normalize(name)
    norm_orig = normalize(original_title)
    
    if norm_name == norm_orig: score += 300
    elif norm_name.startswith(norm_orig): score += 250
    elif norm_orig in norm_name: score += 200
    
    # Check manual substitutions
    if original_title in manual_subs:
        norm_sub = normalize(manual_subs[original_title])
        if norm_sub == norm_name: score += 280
        elif norm_name.startswith(norm_sub): score += 230
        elif norm_sub in norm_name: score += 180
        
    return score

print(f"Reading {INPUT_FILE}...")
with open(INPUT_FILE, "r", encoding="utf-8", errors="ignore") as f:
    lines = f.readlines()

print("Processing lines...")
i = 0
while i < len(lines):
    line = lines[i].strip()
    if line.startswith("#EXTINF"):
        url = lines[i+1].strip() if i + 1 < len(lines) else ""
        display_name = line.split(",")[-1].strip() if "," in line else ""
        norm_display = normalize(display_name)
        
        for title in all_titles:
            targets = [normalize(title)]
            if title in manual_subs:
                targets.append(normalize(manual_subs[title]))
            
            # Split title by ":" or "(" if not matched yet
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

def save_json(titles, filename):
    filmes_list = []
    not_found = 0
    for title in titles:
        match = best_matches[title]
        if match: filmes_list.append({"nome": match['nome'], "url": match['url']})
        else:
            not_found += 1
            filmes_list.append({"nome": f"{title} (Não Encontrado)", "url": ""})
    data = {"tipo": "filmes_batch", "filmes": filmes_list}
    output_path = f"/opt/mdm/data/series/{filename}"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Saved {output_path} - Not found: {not_found}")

save_json(titles1, "desenho1.json")
save_json(titles2, "desenho2.json")
