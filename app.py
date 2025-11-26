from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()


class TextoEntrada(BaseModel):
    texto: str


class TextoSaida(BaseModel):
    resultado: str


ASSINATURA = "ASSINATURA FODA REALIZADA 123456"


@app.get("/")
def read_root():
    return {"status": "ok", "detalhe": "API BPMN parser rodando"}


@app.post("/parse-bpmn", response_model=TextoSaida)
def parse_bpmn(payload: TextoEntrada):
    try:
        texto_original = payload.texto

        if not texto_original or texto_original.strip() == "":
            raise HTTPException(
                status_code=400,
                detail="Campo 'texto' nao pode ser vazio."
            )

        texto_processado = f"{texto_original} {ASSINATURA}"

        return TextoSaida(resultado=texto_processado)

    except HTTPException:
        raise
    except Exception as e:
        print(f"Erro inesperado ao processar texto: {e}")
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao processar o texto."
        )
