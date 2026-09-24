"""Optional Expansion 1.1 screens. Imports are deferred for V1.0 compatibility."""
from command_graphics.painting import GOLD, GREEN, MUTED, RED, TEXT, EDGE
import pygame


def city_page(app, x, y, width):
    from src.engine import cities
    p, game = app.p, app.game
    city = game.cities.get(app.selected_city)
    if city is None:
        y = p.wrap("Select a settlement to fund hospitals, fortifications and local industry. Construction costs are paid up front; each city builds one project at a time.", x, y, width, MUTED, 16)+22
        for city in game.cities.values():
            if city.nation_id != game.player.id:
                continue
            p.button(city.name, (x, y, 260, 38), lambda name=city.name: app.open_city(name))
            p.text(f"{city.population:,} citizens", (x+285, y+8), TEXT, 16)
            p.text(f"{city.local_morale:.0f}% morale", (x+490, y+8), GREEN if city.local_morale>=50 else GOLD, 15)
            queued = city.construction_queue
            status = f"{len(queued)} project(s) / next {queued[0]['weeks_left']} weeks" if queued else "No construction"
            p.text(status, (x+width-300, y+8), MUTED, 14, width=300)
            y += 50
        return y
    p.button("← All settlements", (x, y, 175, 34), lambda: app.open_city(None))
    p.button("Locate on map", (x+190, y, 155, 34), lambda: app.locate(cities.city_location(game, city)))
    y += 55
    p.text(city.name, (x, y), TEXT, 32, True)
    y += 48
    p.text(f"{city.type.upper()}  /  {city.population:,} citizens  /  {city.local_morale:.0f}% local morale", (x, y), GOLD, 17)
    y += 38
    if city.name in game.blockades:
        p.text("PORT BLOCKADED / commercial shipping disrupted", (x,y),RED,16,True)
        y += 32
    if f"city:{city.name}" in game.infections:
        p.text("PUBLIC HEALTH EMERGENCY / review the emergency dispatch",(x,y),RED,16,True)
        y += 32
    built = [cities.buildings(game)[bid]['name'] for bid in city.buildings]
    y = p.wrap("Completed: " + (", ".join(built) or "No local improvements"), x,y,width,MUTED,16)+24
    for bid, spec in cities.buildings(game).items():
        p.text(spec['name'], (x, y), TEXT, 21, True)
        can_build = city.count(bid) < int(spec.get('max',1))
        p.button("Build" if can_build else "Built / queued", (x+width-165,y,150,34),
                 lambda b=bid: app.command(cities.order_building,city.name,b,success=f"Construction ordered in {city.name}."),
                 enabled=can_build and game.player.treasury>=int(spec['cost']))
        y += 31
        y = p.wrap(spec.get('description',''), x,y,width-200,MUTED,15)
        p.text(f"{spec['cost']:,} CR / {spec['weeks']} weeks / limit {spec.get('max',1)}", (x,y), GOLD,14)
        y += 43
    p.text("CONSTRUCTION QUEUE",(x,y),GOLD,13,True)
    y += 30
    if not city.construction_queue:
        p.text("No projects under construction.",(x,y),MUTED,16)
        y += 30
    for i, project in enumerate(city.construction_queue):
        name = cities.buildings(game)[project['building']]['name']
        p.text(f"{i+1:02d}  {name} / {project['weeks_left']} weeks / {'ACTIVE' if i==0 else 'QUEUED'}",(x,y),TEXT,16)
        y += 30
    if city.construction_queue:
        p.button("Cancel last project (50% refund)",(x,y,310,34),lambda:app.command(cities.cancel_building,city.name))
        y += 53
    p.text("LOCAL NEWS WIRE",(x,y),GOLD,13,True)
    y += 30
    if not city.news:
        y = p.wrap("No local dispatches received yet. The news wire updates as weeks resolve.",x,y,width,MUTED,16)
    for news in reversed(city.news):
        p.text(f"WEEK {news['turn']:03d}",(x,y),MUTED,12,True)
        y += 23
        y = p.wrap(news['text'],x,y,width,TEXT,17)+18
    return y


def diplomacy_page(app,x,y,width):
    from src.engine import diplomacy as dip
    p,game=app.p,app.game
    income,_,suspended=dip.trade_income(game,game.player.id)
    port=dip.open_port(game,game.player.id)
    y=p.wrap(f"Foreign trade: {income:,} CR / week   •   Receiving port: {port or 'ALL PORTS BLOCKADED'}",x,y,width,GOLD,18)+12
    if suspended:
        y=p.wrap("Trade suspended by blockade. Convoys may be delayed at sea.",x,y,width,RED,16)+12
    powers=dip.nations(game)
    selected=app.selected_power if app.selected_power in powers else next(iter(powers),None)
    if selected is None:
        return p.wrap("No foreign powers configured.",x,y,width,MUTED,16)
    for nid,data in powers.items():
        p.button(data['name'],(x,y,width-310,37),lambda n=nid:app.open_power(n),active=nid==selected)
        score=dip.relation(game,nid)
        p.text(f"{score:+.0f} / {dip.standing(score)}",(x+width-280,y+9),GREEN if score>0 else GOLD,15)
        y+=45
    y+=20
    data=powers[selected]
    p.text(data['name'],(x,y),TEXT,26,True)
    y+=42
    y=p.wrap(data.get('leader','')+'\n'+data.get('description',''),x,y,width,MUTED,16)+16
    score=dip.relation(game,selected)
    p.text("VOSK  −100",(x,y),RED,12,True)
    p.text("+100  KESTRIA",(x+width-110,y),GREEN,12,True)
    y+=26
    pygame.draw.rect(app.screen,EDGE,(x,y,width,6))
    pygame.draw.circle(app.screen,GOLD,(int(x+width*(score+100)/200),int(y+3)),6)
    y+=27
    cost=game.catalog['diplomacy'].get('gift_cost',25000)
    treaty=game.player.id in dip.foreign(game,selected)['trade']
    p.button(f"Send envoy / {cost:,} CR",(x,y,270,36),lambda:app.command(dip.send_envoy,selected,success="Envoy dispatched. Relations updated."),enabled=game.player.treasury>=cost)
    p.button("Cancel trade treaty" if treaty else "Sign trade treaty",(x+285,y,240,36),
        lambda:app.command(dip.cancel_trade if treaty else dip.sign_trade,selected),
        enabled=treaty or score>=float(data.get('trade_min_alignment',10)))
    y+=51
    y=p.wrap(f"Trade treaty requires {data.get('trade_min_alignment',10):+g} alignment; yields {data.get('trade_income_bonus_cr',0):,} CR per week while ports remain open.",x,y,width,MUTED,15)+24
    p.text("LEND-LEASE PROCUREMENT",(x,y),GOLD,13,True)
    y+=32
    offers=dip.packages(game,selected)
    if not offers:
        y=p.wrap("This power offers no lend-lease packages.",x,y,width,MUTED,16)+12
    for offer in offers:
        eligible=score>=offer['min_alignment'] and game.player.treasury>=offer['cost']
        p.text(offer['name'],(x,y),TEXT,20,True,width=width-220)
        p.button("Purchase cargo",(x+width-190,y,175,34),
            lambda oid=offer['id']:app.command(dip.buy_lend_lease,selected,oid,success="Cargo purchased. Track its convoy below."),enabled=eligible)
        y+=33
        contents=", ".join(f"{n:,} {item.replace('_',' ')}" for item,n in offer['items'].items())
        y=p.wrap(contents,x,y,width-210,MUTED,15)
        y=p.wrap(f"{offer['cost']:,} CR / {offer['weeks']} weeks at sea / requires alignment {offer['min_alignment']:+g}",x,y,width,GOLD,14)+24
    p.text("YOUR CONVOYS",(x,y),GOLD,13,True)
    y+=31
    shipments=[s for s in game.shipments if s['to']==game.player.id]
    if not shipments:
        y=p.wrap("No cargo currently at sea.",x,y,width,MUTED,16)
    for shipment in shipments:
        y=p.wrap(f"{shipment['id']} / {shipment['name']} / {shipment['weeks_left']} weeks / {shipment['status']}",x,y,width,TEXT,16)+12
    return y
