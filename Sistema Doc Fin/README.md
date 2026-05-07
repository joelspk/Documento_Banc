# Combinador de PDFs FUNDUNESP

Aplicacao web para juntar PDFs de oficios e relatorios bancarios, com regra de pareamento por finalidade do oficio e compatibilidade de contas.

## Rodar localmente

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Abra: http://127.0.0.1:8123

## Senha opcional

Defina a variavel `APP_PASSWORD`. O usuario pode ser qualquer texto; a senha precisa bater. Exemplo:

```bash
APP_PASSWORD=minha-senha python app.py
```

## Publicar no Render

1. Crie um repositorio no GitHub com estes arquivos.
2. No Render, clique em **New > Web Service**.
3. Conecte o repositorio.
4. Use:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python app.py`
5. Em **Environment**, cadastre `APP_PASSWORD` para proteger o acesso.

Tambem e possivel usar o arquivo `render.yaml` para Blueprint.

## Regras implementadas

- Processamento Eletronico -> usa Relatorio Bancario de Conferencia.
- Impostos -> usa Relatorio Bancario de Conferencia.
- Transferencia de mesma titularidade -> usa TRANSFERENCIA ENTRE CONTAS DA MESMA TITULARIDADE.
- Compatibilidade de contas:
  - aceita numeros iguais apos remover mascara;
  - aceita oficio com ultimo digito a mais, removendo o ultimo digito para comparar;
  - preserva casos como BTG em que oficio e relatorio sao iguais.
- Oficio FIN com varias paginas entra apenas com a pagina 1 para evitar duplicacao dos relatorios individuais.

## Observacoes importantes

- Em hospedagens gratuitas, o armazenamento e temporario. Baixe o PDF final logo apos o processamento.
- Para muitos arquivos grandes, aumente `MAX_UPLOAD_MB` e prefira um plano com mais memoria.
