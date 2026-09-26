#!/usr/bin/env python3
"""Outils de base : fréquences, indice de coïncidence, César, Vigenère.

Usage :
    python3 analyse.py freq    fichier.txt
    python3 analyse.py ic      fichier.txt
    python3 analyse.py cesar   fichier.txt          # essaie les 26 décalages
    python3 analyse.py vigenere fichier.txt CLE     # déchiffre avec CLE
"""
import string
import sys
from collections import Counter

ALPHABET = string.ascii_uppercase
# Fréquences approximatives des lettres en français (%)
FREQ_FR = {
    "E": 14.7, "A": 7.6, "I": 7.5, "S": 7.9, "N": 7.1, "R": 6.6, "T": 7.2,
    "O": 5.8, "L": 5.5, "U": 6.3, "D": 3.7, "C": 3.3, "M": 3.0, "P": 3.0,
    "G": 0.9, "B": 0.9, "V": 1.6, "H": 0.7, "F": 1.1, "Q": 1.4, "Y": 0.3,
    "X": 0.4, "J": 0.5, "K": 0.05, "W": 0.04, "Z": 0.1,
}


def lettres(texte):
    return [c for c in texte.upper() if c in ALPHABET]


def frequences(texte):
    compte = Counter(lettres(texte))
    total = sum(compte.values()) or 1
    return {l: 100 * compte[l] / total for l in ALPHABET}


def indice_coincidence(texte):
    compte = Counter(lettres(texte))
    n = sum(compte.values())
    if n < 2:
        return 0.0
    return sum(k * (k - 1) for k in compte.values()) / (n * (n - 1))


def score_francais(texte):
    """Plus le score est bas, plus le texte ressemble à du français (chi²)."""
    freq = frequences(texte)
    return sum((freq[l] - FREQ_FR[l]) ** 2 / FREQ_FR[l] for l in ALPHABET)


def decaler(texte, k):
    res = []
    for c in texte:
        u = c.upper()
        if u in ALPHABET:
            d = ALPHABET[(ALPHABET.index(u) - k) % 26]
            res.append(d if c.isupper() else d.lower())
        else:
            res.append(c)
    return "".join(res)


def vigenere(texte, cle):
    cle = [ALPHABET.index(c) for c in cle.upper() if c in ALPHABET]
    res, i = [], 0
    for c in texte:
        if c.upper() in ALPHABET:
            res.append(decaler(c, cle[i % len(cle)]))
            i += 1
        else:
            res.append(c)
    return "".join(res)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, chemin = sys.argv[1], sys.argv[2]
    with open(chemin, encoding="utf-8") as f:
        texte = f.read()

    if cmd == "freq":
        for l, p in sorted(frequences(texte).items(), key=lambda x: -x[1]):
            print(f"{l} {p:5.2f}%  (fr {FREQ_FR[l]:5.2f}%)")
    elif cmd == "ic":
        print(f"IC = {indice_coincidence(texte):.4f}  (français ≈ 0.078, aléatoire ≈ 0.038)")
    elif cmd == "cesar":
        essais = sorted(range(26), key=lambda k: score_francais(decaler(texte, k)))
        for k in essais:
            print(f"[{k:2}] {decaler(texte, k)[:80]!r}")
    elif cmd == "vigenere" and len(sys.argv) > 3:
        print(vigenere(texte, sys.argv[3]))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
