import os, re, sys, io, logging
S="/tmp/portal-smoke"
import shutil; shutil.rmtree(S, ignore_errors=True); os.makedirs(S)
os.environ.update(SECRET_KEY="x"*32, ADMIN_PHONES="09120000000", SUB_BASE_URL="https://sub.t/sub",
  DATA_DIR=S, DB_PATH=S+"/p.db", PLANS_FILE="plans.json", COOKIE_SECURE="false")
sys.path.insert(0,".")
from app import main, sms
codes={}
sms.send_otp=lambda p,c: codes.__setitem__(p,c)
calls=[]
class Fake:
    def add_clients(self,*a): calls.append(("add",a)); return [[1,"p1-1-1"]]
    def remove(self,c): calls.append(("rm",c))
    def usage(self,c): return {"used":5*1024**3,"total":50*1024**3,"expiry":-30*86400000}
main.panel=Fake()
from fastapi.testclient import TestClient
JPG=b"\xff\xd8\xff\xe0"+b"0"*100
def login(cl,phone):
    r=cl.post("/login",data={"phone":phone}); assert r.status_code==200 and "کد تأیید" in r.text, r.text[:300]
    p=main.kyc.norm_phone(phone)
    r=cl.post("/verify",data={"code":"000000" if codes[p]!="000000" else "111111"}); assert "نادرست" in r.text
    r=cl.post("/verify",data={"code":codes[p]}); return r
with TestClient(main.app) as u, TestClient(main.app) as a:
    r=login(u,"+98 912 111 2233"); assert "احراز هویت" in r.text
    r=u.post("/kyc",data={"full_name":"علی تست","national_code":"0000000001"},files={"id_doc":("a.jpg",JPG),"selfie":("b.jpg",JPG)}); assert "معتبر نیست" in r.text
    r=u.post("/kyc",data={"full_name":"علی تست","national_code":"۰۰۱۲۳۴۵۶۷۸"},files={"id_doc":("a.jpg",b"notimg"),"selfie":("b.jpg",JPG)})
    print("nc 0012345678 valid?", main.kyc.valid_national_code("0012345678"))
    nc="0499370899"; assert main.kyc.valid_national_code(nc)
    r=u.post("/kyc",data={"full_name":"علی تست","national_code":nc},files={"id_doc":("a.jpg",b"notimg"),"selfie":("b.jpg",JPG)}); assert "JPG" in r.text
    r=u.post("/kyc",data={"full_name":"علی تست","national_code":nc},files={"id_doc":("a.jpg",JPG),"selfie":("b.jpg",JPG)}); assert "در حال بررسی" in r.text
    assert u.get("/admin").status_code==404
    r=login(a,"09120000000"); assert "احراز هویت در انتظار" in r.text and "علی تست" in r.text
    img=re.search(r'/admin/file/([0-9a-f]{32}\.jpg)',r.text).group(1)
    assert a.get("/admin/file/"+img).status_code==200 and u.get("/admin/file/"+img).status_code==404
    uid=1
    r=a.post(f"/admin/kyc/{uid}",data={"action":"approve"}); assert "تأیید شد" in r.text
    assert not os.path.exists(f"{S}/kyc/{img}"), "purge failed"
    r=u.get("/dashboard"); assert "دریافت سرویس" in r.text and "درخواست" in r.text
    r=u.post("/request",data={"plan_id":"m1-50"}); assert "به‌زودی" in r.text
    r=u.post("/request",data={"plan_id":"m1-50"}); assert "فعال یا در انتظار" in r.text
    r=a.get("/admin"); assert "#1" in r.text
    r=a.post("/admin/request/1",data={"action":"approve"}); assert "فعال شد" in r.text, r.text[-500:]
    r=u.get("/dashboard"); assert "https://sub.t/sub/" in r.text and "<svg" in r.text and "5.0 از 50.0" in r.text
    r=a.post("/admin/request/1",data={"action":"revoke"}); assert "غیرفعال شد" in r.text
    r=u.get("/dashboard"); assert "غیرفعال" in r.text
    print("headers:", {k:v for k,v in r.headers.items() if k.lower() in ("content-security-policy","server")})
    low=r.text.lower()
    for w in ["vless","vpn","xray","x-ui","proxy","v2ray","فیلتر","پروکسی","وی پی ان"]:
        assert w not in low, w
    print("calls:", [c[0] for c in calls])
print("ALL OK")
