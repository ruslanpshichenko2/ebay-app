#!/bin/zsh

cd /Users/ruslanpshichenko/ebay-app || exit 1

if [ -f "$HOME/.zprofile" ]; then
  source "$HOME/.zprofile"
fi

if [ -f "$HOME/.zshrc" ]; then
  source "$HOME/.zshrc"
fi

python3 -m streamlit run app.py
