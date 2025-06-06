
# Rauðsteinssmokkfiskur

Þetta er discord vélmenni sem er hannað til að gera ferlið við að senda inn, staðfesta og hafna innsendingum auðveldara. Að auki stjórnar það gagnagrunninum yfir mets sjálfkrafa.

## Er að fá Hafist handa

Að setja upp þína eigin útgáfu af þessum vélmenni er **EKKI Mælt með** þar sem það er nú þegar tilvik í gangi sem þú getur boðið á discord þjóninn þinn. Ef þú býrð til þitt eigið tilvik mun það hafa sérstakan gagnagrunn við það sem þegar er í gangi. Ef þú vilt nota þennan vélmenni, slepptu því í `Discord Uppsetning`.

Til að koma þessu botni í gang á vélinni þinni þarftu afrit af þessari geymslu. Til að klóna geymsluna skaltu nota:
```bash
git clone https://github.com/Kappeh/Redstone-Squid.git
```
Þá geturðu farið í rótarskrá geymslunnar með
```bash
cd Redstone-Squid
```

### Sýndarumhverfi

Það er listi yfir nauðsynlega python pakka í requirements.txt. Þú getur sett þau upp á vélina þína beint eða í sýndarumhverfi (mælt með)

Ef þú vilt nota sýndarumhverfi skaltu fyrst búa til umhverfið í rótarskránni og virkja það.
```bash
python -m venv venv
source venv/Lib/activate
```
Að öðrum kosti geturðu sett upp conda umhverfi með:
```bash
conda create -f environment.yml
conda activate redstone-squid
```

### Er að setja upp Pakkar

Í rótarskrá geymslunnar geturðu notað eftirfarandi skipun til að setja upp alla nauðsynlega pakka.
```bash
pip install -r requirements.txt
```

### Persónuskilríkisskrár

Google þjónustur krefjast Google þjónustureiknings. Þú getur lesið um þjónustureikninga Google á https://cloud.google.com/iam/docs/understanding-service-accounts. Sæktu persónuskilríki JSON skrána og endurnefna hana `client_secret.json` og færðu hana í `Google` skrána.

Discord krefst discord bot reikning. Þú getur lært hvernig á að búa til vélmennareikninga á https://github.com/reactiflux/discord-irc/wiki/Creating-a-discord-bot-&-getting-a-token. Þú þarft táknið til að vera sett í skrá sem heitir `auth.ini` í rótarskránni með eftirfarandi innihaldi:
```
[discord]
token = <Skiptu þessu út fyrir discord aðgangsmerkið þinn>
```

Supabase er gagnagrunnurinn sem notaður er fyrir þennan botn. Þú getur skráð þig fyrir ókeypis reikning á https://supabase.com/. Þegar þú ert kominn með reikning geturðu búið til nýtt verkefni og farið í **Project Settings | API** og afritaðu vefslóðina og API lykilinn (leyndarmál, ekki opinbert) á sama `auth.ini` með eftirfarandi innihaldi:
```
[supabase]
SUPABASE_URL = <Replace this with your supabase url>
SUPABASE_KEY = <Replace this with your supabase api key>
```
Schema fyrir gagnagrunninn hefur ekki verið gefið upp vegna þess að ég hef ekki skipulagt það ennþá. Ef þú vilt keyra þennan botn, vinsamlegast hafðu samband við mig (@papetoast á discord) og ég mun útvega þér schema.

### Er að keyra Forritið

Forritið er nú hægt að keyra einfaldlega með:
```
python app.py
```

## Discord Uppsetning

### Er að bæta botni við netþjóna
Þú getur bætt botni þínum við netþjóninn þinn með því að fara á `https://discordapp.com/oauth2/authorize?client_id=<SKIPTI MEÐ AUÐKENNI BOTTAR>&scope=bot`. Mælt er með því að gefa því stjórnandaheimildir en er ekki krafist vegna virkni þess.

Ef þú vilt bjóða aðaltilvikinu á netþjóninn þinn, smelltu [hér](https://discordapp.com/oauth2/authorize?client_id=528946065668308992&scope=bot&permissions=8).

### Er að setja upp Rásir

Áður en botninn getur sent einhverjar mets á netþjóninn þinn verður þú að segja honum hér til að senda hvern flokk. Hægt er að stilla marga flokka á eina rás.

Til að gefa dæmi, láttu okkur gera ráð fyrir að þú viljir stilla öll flokkana til að pósta á rás sem kallast `#met`. Innan discord netþjónsins myndirðu keyra:
```
!settings smallest_channel set #met
!settings fastest_channel set #met
!settings first_channel set #met
```
Í hvert sinn sem innsending er staðfest af stjórnendum vélmennisins verður hún sett á viðkomandi rás.

Þú getur afstillt rás með því annað hvort að stilla hana á aðra rás eða keyra unset skipunina t.d.
```
!settings unset smallest_channel
```
Auk þessa er hægt að athuga á hvaða rás stilling er stillt á með fyrirspurnarskipuninni t.d.
```
!settings query fastest_channel
```
Ef þú vilt spyrjast fyrir um allar stillingar í einu geturðu keyrt:
```
!settings query_all
```

## Aðrar skipanir

Þessi listi yfir skipanir getur breyst vegna endurbóta og nýrra eiginleika. Reyndar veitir `discord.py` sjálfskjalandi hjálparskilaboð fyrir hverja skipun, svo þú getur alltaf keyrt `!help` til að sjá nýjasta lista yfir skipanir.

* `!invite_link` gefur notandanum hlekk sem hann getur notað til að bæta botni við netþjóna sína.
* `!source_code` tengir notanda við þessa GitHub geymslu.
* `!submit_record` veitir notanda Google eyðublaðið sem er notað til að safna innsendingum.
* Rætt hefur verið um `!settings` hér að ofan.
* `!submissions` er netþjónssértækt, hlutverksákveðið sett af skipunum sem notað er til að skoða, staðfesta og hafna innsendingum. _Fjallað verður um þetta hér að neðan._
* `!help <skipun>` veitir notanda hjálparskilaboð. Ef skipun er veitt verða hjálparskilaboð fyrir þá skipun veitt.

### Uppgjöf skipanir

`!submissions open` veitir yfirlit yfir innsendur sem eru opnar til skoðunar.
`!submissions view <vísitölu>` sýnir heildaruppgjöfina með tiltekinni vísitölu.
`!submission confirm <vísitölu>` staðfestir innsendingu og birtir hana á réttar rásir.
`!submission deny <vísitölu>` neitar innsendingu.

## Er að leggja sitt af mörkum

Vinsamlega lestu [CODE_OF_CONDUC.md](https://github.com/Kappeh/Redstone-Squid/blob/master/CODE_OF_CONDUCT.md) til að fá upplýsingar um siðareglur okkar og ferlið við að senda okkur draga beiðnir.

## Höfundar

* **Kieran Powell** - *Upphafsverk* - [Kappeh](https://github.com/Kappeh)
* **Savio Mak** - *Hjálparaðili* - [Glinte](https://github.com/Glinte)

## Leyfi

Þetta verkefni er með leyfi samkvæmt MIT leyfinu - sjá [LICENSE](../../LICENSE) skrána fyrir frekari upplýsingar

## Viðurkenningar

- Takk fyrir tæknilega Minecraft samfélagið fyrir að hafa mig.
- Discord og Google eru æðisleg. Takk fyrir frábær API og skjöl.
- Þakkir til alls fólksins á StackOverflow fyrir hjálpina og stuðninginn.
