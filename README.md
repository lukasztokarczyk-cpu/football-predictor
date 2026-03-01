# ⚽ System Predykcji Meczów Piłkarskich

Zaawansowany system predykcji wyników oparty na modelu Poissona, rankingach ELO i analizie formy.

## 🏗️ Struktura

```
football_predictor/
├── main.py          # Główny skrypt - pipeline predykcji + CLI + eksport CSV
├── api.py           # Klient API-Football (RapidAPI) z cache i rate limitingiem
├── model.py         # Model predykcyjny: Poisson + ELO + forma + kontuzje
├── requirements.txt # Zależności Python
├── .env.example     # Przykładowy plik konfiguracyjny
└── README.md        # Ta dokumentacja
```

## ⚙️ Instalacja

```bash
# 1. Zainstaluj zależności
pip install -r requirements.txt

# 2. Skopiuj i skonfiguruj plik .env
cp .env.example .env
# Edytuj .env i wstaw klucz API
```

## 🔑 Klucz API

1. Zarejestruj się na [RapidAPI](https://rapidapi.com/api-sports/api/api-football)
2. Subskrybuj API-Football (darmowy plan: 100 req/dzień)
3. Skopiuj klucz do pliku `.env`:
   ```
   RAPIDAPI_KEY=twoj_klucz_tutaj
   ```

## 🚀 Użycie

```bash
# Tryb demo (bez klucza API - syntetyczne dane)
python main.py --demo

# Mecze dziś + jutro (domyślny)
python main.py

# Tylko mecze dziś
python main.py --mode today

# Tylko mecze jutro
python main.py --mode tomorrow

# Wszystkie dostępne mecze (popularne ligi)
python main.py --mode all

# Premier League (ID: 39) - mecze dziś
python main.py --mode today --league 39

# Konkretny sezon
python main.py --season 2024

# Własna ścieżka CSV
python main.py --output moje_typy.csv

# Tryb debug (więcej logów)
python main.py --debug
```

### Popularne ID lig
| Liga | ID |
|------|----|
| Premier League | 39 |
| La Liga | 140 |
| Bundesliga | 78 |
| Serie A | 135 |
| Ligue 1 | 61 |
| Ekstraklasa | 106 |
| Champions League | 2 |
| Europa League | 3 |

## 🧠 Algorytm

### 1. Model Poissona (model.py)
Liczba goli każdej drużyny traktowana jest jako niezależna zmienna losowa z rozkładu Poissona z parametrem λ.

Parametr λ uwzględnia:
- Siłę ataku i słabość obrony (Dixon-Coles)
- Formę z ostatnich 5 meczów (liniowe ważenie)
- Korektę ELO dla relatywnej siły drużyn
- Karę za brakujących kluczowych zawodników

### 2. Rankingi ELO
System ELO adaptowany dla piłki nożnej:
- Remis = 0.5 punktu (zamiast 0 jak w szachach)
- Przewaga własnego boiska: +100 punktów ELO
- K-faktor: 32 (reaktywny, ale stabilny)

### 3. Analiza formy
Ostatnie 5 meczów z liniowym ważeniem (najnowszy mecz = waga 5, najstarszy = waga 1).

### 4. Kontuzje i zawieszenia
Za każdego niedostępnego napastnika/pomocnika -5% siły ataku (max -25%).

## 📊 Wyniki
- Prawdopodobieństwa 1/X/2 w procentach
- Oczekiwana liczba goli
- Najbardziej prawdopodobny wynik (np. 2:1)
- Top 5 wyników z prawdopodobieństwami
- Eksport do CSV z pełnymi danymi

## ⚠️ Ograniczenia
- Darmowy plan API: 100 zapytań/dzień (ok. 5-10 meczów z pełnymi danymi)
- Model nie uwzględnia pogody, presji tytułowej, ani rozgrywek równoległych
- ELO jest budowane tylko na podstawie meczów z bieżącego dnia - dla produkcji należy przechowywać historię ratingów w bazie danych
