from django.db import migrations


COMMUNES_NORD = [
    # Arrondissement de Cap-Haïtien
    {"name": "Cap-Haïtien", "slug": "cap-haitien", "arrondissement": "Cap-Haïtien", "lat": 19.7578, "lng": -72.2042},
    {"name": "Quartier-Morin", "slug": "quartier-morin", "arrondissement": "Cap-Haïtien", "lat": 19.6975, "lng": -72.1558},
    {"name": "Limonade", "slug": "limonade", "arrondissement": "Cap-Haïtien", "lat": 19.6739, "lng": -72.1333},

    # Arrondissement d'Acul-du-Nord
    {"name": "Acul-du-Nord", "slug": "acul-du-nord", "arrondissement": "Acul-du-Nord", "lat": 19.6833, "lng": -72.3167},
    {"name": "Plaine-du-Nord", "slug": "plaine-du-nord", "arrondissement": "Acul-du-Nord", "lat": 19.7167, "lng": -72.2667},
    {"name": "Milot", "slug": "milot", "arrondissement": "Acul-du-Nord", "lat": 19.6067, "lng": -72.2167},

    # Arrondissement de Grande-Rivière-du-Nord
    {"name": "Grande-Rivière-du-Nord", "slug": "grande-riviere-du-nord", "arrondissement": "Grande-Rivière-du-Nord", "lat": 19.5750, "lng": -72.1667},
    {"name": "Bahon", "slug": "bahon", "arrondissement": "Grande-Rivière-du-Nord", "lat": 19.4667, "lng": -72.1333},

    # Arrondissement de Saint-Raphaël
    {"name": "Saint-Raphaël", "slug": "saint-raphael", "arrondissement": "Saint-Raphaël", "lat": 19.4333, "lng": -72.1000},
    {"name": "Dondon", "slug": "dondon", "arrondissement": "Saint-Raphaël", "lat": 19.5300, "lng": -72.2386},
    {"name": "Ranquitte", "slug": "ranquitte", "arrondissement": "Saint-Raphaël", "lat": 19.4167, "lng": -71.9500},
    {"name": "Pignon", "slug": "pignon", "arrondissement": "Saint-Raphaël", "lat": 19.3333, "lng": -72.1167},
    {"name": "La Victoire", "slug": "la-victoire", "arrondissement": "Saint-Raphaël", "lat": 19.3833, "lng": -71.9833},

    # Arrondissement de Borgne
    {"name": "Borgne", "slug": "borgne", "arrondissement": "Borgne", "lat": 19.8500, "lng": -72.5333},
    {"name": "Port-Margot", "slug": "port-margot", "arrondissement": "Borgne", "lat": 19.7500, "lng": -72.4333},

    # Arrondissement de Limbé
    {"name": "Limbé", "slug": "limbe", "arrondissement": "Limbé", "lat": 19.7000, "lng": -72.4000},
    {"name": "Bas-Limbé", "slug": "bas-limbe", "arrondissement": "Limbé", "lat": 19.7833, "lng": -72.4333},

    # Arrondissement de Plaisance
    {"name": "Plaisance", "slug": "plaisance", "arrondissement": "Plaisance", "lat": 19.6000, "lng": -72.4667},
    {"name": "Pilate", "slug": "pilate", "arrondissement": "Plaisance", "lat": 19.6667, "lng": -72.5500},
]


def seed_communes(apps, schema_editor):
    db_name = str(schema_editor.connection.settings_dict.get("NAME", ""))
    if db_name.startswith("test_"):
        return

    Location = apps.get_model("locations", "Location")

    # Deactivate existing non-Nord locations so only Nord communes are listed
    nord_slugs = [c["slug"] for c in COMMUNES_NORD]
    Location.objects.exclude(slug__in=nord_slugs).update(is_active=False)

    for item in COMMUNES_NORD:
        loc = Location.objects.filter(slug__iexact=item["slug"]).first()
        if loc:
            loc.name = item["name"]
            loc.slug = item["slug"]
            loc.type = "COMMUNE"
            loc.description = f"Arrondissement de {item['arrondissement']}"
            loc.latitude = item["lat"]
            loc.longitude = item["lng"]
            loc.is_active = True
            loc.save()
        else:
            Location.objects.create(
                name=item["name"],
                slug=item["slug"],
                type="COMMUNE",
                description=f"Arrondissement de {item['arrondissement']}",
                latitude=item["lat"],
                longitude=item["lng"],
                is_active=True,
            )


def reverse_seed_communes(apps, schema_editor):
    Location = apps.get_model("locations", "Location")
    nord_slugs = [c["slug"] for c in COMMUNES_NORD]
    Location.objects.filter(slug__in=nord_slugs).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("locations", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_communes, reverse_seed_communes),
    ]
