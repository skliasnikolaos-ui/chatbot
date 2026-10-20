from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...config import settings
from ...security import authenticate, create_session, revoke_session
from ...services import audit
from ..app import templates
from ..deps import SESSION_COOKIE, Ctx, auth, form, get_db

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    f = await request.form()
    user = authenticate(db, f.get("email", ""), f.get("password", ""))
    if user is None:
        audit.log(db, None, None, "auth.login_failed", "user", None)
        db.commit()
        return templates.TemplateResponse(request, "login.html",
                                          {"error": "Λάθος στοιχεία ή προσωρινό κλείδωμα λογαριασμού."}, status_code=401)
    token, _ = create_session(db, user)
    audit.log(db, user.company_id, user.id, "auth.login", "user", user.id)
    db.commit()
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="strict",
                    max_age=settings.session_hours * 3600)
    return resp


@router.post("/logout")
async def logout(request: Request, ctx: Ctx = Depends(auth)):
    await form(request, ctx)
    revoke_session(ctx.db, request.cookies.get(SESSION_COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp
