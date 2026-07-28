# Material Library Sources and Calibration Policy

Cloth NeXt contains two deliberately different kinds of material presets:

1. **Scientific fabric profiles** derived from published laboratory datasets.
2. **Branded Product Samples** based on public manufacturer specifications or documented commercial samples.

Product names are used only to identify the referenced material or construction. Their inclusion does not imply endorsement, sponsorship, certification, or a claim that Cloth NeXt produces an exact digital twin. All trademarks belong to their respective owners.

## What the data-quality labels mean

| Label | Meaning |
| --- | --- |
| `SCIENTIFIC_MEASUREMENT` | A compatible property was measured in a published scientific dataset. |
| `OFFICIAL_PRODUCT_DATA` | The manufacturer published the cited construction and/or areal weight. |
| `PUBLISHED_PRODUCT_SAMPLE` | A documented commercial sample published the cited construction or weight. |
| `CALIBRATED_REFERENCE` | The product identity or technology is official, while mass and solver behavior are representative Cloth NeXt calibration values. |

## What is calibrated

PPF uses density-normalized shell parameters rather than a complete textile constitutive model. Except where explicitly stated otherwise, these fields are **Cloth NeXt artist calibrations**:

- Stretch Resistance
- Sideways Response
- Bend Resistance
- Surface Grip
- Maximum Stretch
- damping and contact values

A published areal weight is mapped directly to `surface_weight` in kg/m². Product construction, thickness, loft, foam density, waterproofing, tensile strength, tear strength, abrasion ratings, and marketing performance claims are not silently converted into laboratory PPF values.

The current shell solver represents a material as a surface. Thick products such as neoprene, Thinsulate and lofted insulation therefore reproduce overall sheet motion and drape, not volumetric foam compression, thermal behavior or certified protective performance.

## Scientific dataset

The 30 MIT profiles retain the existing provenance documented in `ppf_fabric_presets.toml`:

- Bouman et al., *Estimating the Material Properties of Fabric from Video*, ICCV 2013.
- MIT Fabric Properties Dataset.
- Area weight is converted from oz/yd² to kg/m².
- Compatible bending measurements are mapped using the documented Cloth NeXt/PPF calibration. Other PPF-only controls remain category calibrations.

## Branded Product Samples

### Outdoor laminates and active insulation

| Source ID | Product reference | Evidence used |
| --- | --- | --- |
| `PRODUCT_SOURCE_GORETEX_ARCTIC_3L` | GORE-TEX Arctic Stretch 3-Ply | Commercial 180 g/m² sample: https://lestissees.com/products/gore-tex-arctic-stretch-3-ply ; official laminate context: https://www.gore-tex.com/technology/gore-tex-products/pro |
| `PRODUCT_SOURCE_XPAC_VX21` | X-Pac VX21 | Official construction and 210 g/m²: https://www.x-pac.com/product/vx21/ |
| `PRODUCT_SOURCE_XPAC_VX21_SOFT` | X-Pac VX21 Soft | Official construction and 188 g/m²: https://www.x-pac.com/product/vx21/ |
| `PRODUCT_SOURCE_XPAC_LS21` | X-Pac LS21 | Official construction and 214 g/m²: https://www.x-pac.com/product/ls21/ |
| `PRODUCT_SOURCE_POLARTEC_ALPHA_4004` | Polartec Alpha Direct style 4004 | Official style and 85 g/m²: https://www.polartec.com/alpha-rewind |
| `PRODUCT_SOURCE_POLARTEC_ALPHA_4411RC` | Polartec Alpha Direct style 4411RC | Official style and 119 g/m²: https://www.polartec.com/alpha-rewind |
| `PRODUCT_SOURCE_POLARTEC_ALPHA_4024` | Polartec Alpha Direct style 4024 | Official style and 186 g/m²: https://www.polartec.com/alpha-rewind |
| `PRODUCT_SOURCE_POLARTEC_ALPHA_4048` | Polartec Alpha Direct with Wool style 4048 | Official style and 153 g/m²: https://www.polartec.com/alpha-rewind |
| `PRODUCT_SOURCE_CHALLENGE_ULTRA_100` | Challenge ULTRA 100 | Official construction and 99 g/m²: https://www.challengesailcloth.com/ultra-collection |
| `PRODUCT_SOURCE_CHALLENGE_ULTRA_200` | Challenge ULTRA 200 | Official construction and 119 g/m²: https://www.challengesailcloth.com/ultra-collection |
| `PRODUCT_SOURCE_DYNEEMA_CT1E08` | Dyneema Composite Fabric 0.55 CT1E.08 | Official construction and 19 g/m²: https://www.dyneema.com/fabric-finder/dyneema-composite-fabric-055/ct1e08 |
| `PRODUCT_SOURCE_DYNEEMA_CT5K18` | Dyneema Composite Fabric 1.6 CT5K.18 | Official construction and 53 g/m²: https://dyneema.com/fabric-finder/dyneema-composite-fabric-16/ct5k18-black |

### Performance, stretch and neoprene

| Source ID | Product reference | Evidence used |
| --- | --- | --- |
| `PRODUCT_SOURCE_ADIDAS_TIRO25_MATCH` | adidas Tiro 25 Competition Match Jersey | Official recycled-polyester, AEROREADY and mesh construction: https://www.adidas.com/us/tiro-25-competition-match-jersey/JF6085.html ; mass is calibrated. |
| `PRODUCT_SOURCE_ADIDAS_TIRO25_TRAINING` | adidas Tiro 25 Competition Training Jersey | Official recycled-polyester, AEROREADY and mesh construction: https://www.adidas.com/us/tiro-25-competition-training-jersey/JJ1520.html ; mass is calibrated. |
| `PRODUCT_SOURCE_POLARTEC_POWER_GRID` | Polartec Power Grid | Official bi-component grid-knit technology: https://www.polartec.com/fabrics/base/power-grid ; mass is calibrated. |
| `PRODUCT_SOURCE_LYCRA_SPORT` | LYCRA SPORT technology | Official activewear technology: https://one.lycra.com/en/business/search-technologies/lycra-sport-technology ; mass is calibrated. |
| `PRODUCT_SOURCE_COOLMAX_ECOMADE` | COOLMAX EcoMade technology | Official moisture-management technology: https://one.lycra.com/en/business/search-technologies/coolmaxr-ecomade-technology ; mass is calibrated. |
| `PRODUCT_SOURCE_TENCEL_LYOCELL` | TENCEL Lyocell | Official fiber properties: https://www.tencel.com/fibers ; jersey construction and mass are calibrated. |
| `PRODUCT_SOURCE_YAMAMOTO_39` | Yamamoto #39 SuperLight | Official closed-cell limestone chloroprene identity: https://yamamoto-bio.com/material-e/39superlight.html ; 3 mm laminate mass is calibrated. |
| `PRODUCT_SOURCE_YAMAMOTO_40` | Yamamoto #40 SuperStretch | Official higher-flexibility chloroprene identity: https://yamamoto-bio.com/material-e/40superstretch.html ; 3 mm laminate mass is calibrated. |
| `PRODUCT_SOURCE_NEOPRENE_3MM_SAMPLE` | 3 mm double-jersey CR foam | Published 3 mm commercial construction plus representative CR foam density: https://www.ramgaskets.com/wp-content/uploads/2020/08/N170-Neoprene-Foam.pdf ; surface mass is derived and solver behavior is calibrated. |

### Protective and abrasion materials

| Source ID | Product reference | Evidence used |
| --- | --- | --- |
| `PRODUCT_SOURCE_KEVLAR_K29_7451S` | DuPont Kevlar K29 745GR/7451S | Official plain-weave identity, armor use and 465 g/m² conditioned weight: https://www.dupont.co.uk/content/dam/dupont/amer/us/en/safety/public/documents/en/DuPont__Kevlar__Vehicle_Armor_Brochure.pdf |
| `PRODUCT_SOURCE_NOMEX_450A` | DuPont Nomex Essential 450A | Official blend, plain weave and 150 g/m²: https://www.dupont.com/life-protection/fr-uniforms.html |
| `PRODUCT_SOURCE_NOMEX_ARC650` | DuPont Nomex Essential Arc 650 | Official blend, twill and 220 g/m²: https://www.dupont.com/life-protection/fr-uniforms.html |
| `PRODUCT_SOURCE_TYVEK_400` | DuPont Tyvek 400 | Official product specification and 41.5 g/m²: https://www.dupont.com/content/dupont/apac/ap/en/products/personal-protection/safespec/tyvek-400-model-ty351s-wh.html |
| `PRODUCT_SOURCE_CORDURA_CLASSIC` | CORDURA Classic 500D / 1000D | Official high-tenacity nylon 6,6 construction: https://cordura.com/classic-fabric ; mass and PPF behavior are calibrated. |
| `PRODUCT_SOURCE_CORDURA_BALLISTIC` | CORDURA Ballistic 1680D | Official ballistic fabric family: https://cordura.com/ballistic-fabric ; mass and PPF behavior are calibrated. |

### Shells and softshells

| Source ID | Product reference | Evidence used |
| --- | --- | --- |
| `PRODUCT_SOURCE_GORETEX_PRO_3L` | GORE-TEX PRO 3-Layer | Official three-layer protective laminate family: https://www.gore-tex.com/technology/gore-tex-products/pro ; mass is calibrated. |
| `PRODUCT_SOURCE_PERTEX_QUANTUM` | Pertex Quantum | Official tightly woven, downproof, sub-25 g/m² family: https://pertex.com/fabrics-technologies/quantum |
| `PRODUCT_SOURCE_PERTEX_QUANTUM_AIR` | Pertex Quantum Air | Official open-woven active-shell construction: https://pertex.com/fabrics-technologies/quantum-air ; mass is calibrated. |
| `PRODUCT_SOURCE_PERTEX_SHIELD_AIR` | Pertex Shield Air | Official electrospun membrane laminate: https://pertex.com/fabrics-technologies/shield-air ; mass is calibrated. |
| `PRODUCT_SOURCE_SCHOELLER_DRYSKIN` | schoeller-dryskin | Official performance double-fabric family: https://www.schoeller-textiles.com/en/textiles/sport ; mass is calibrated. |
| `PRODUCT_SOURCE_SCHOELLER_WB400` | schoeller-wb-400 | Official bonded softshell family: https://www.schoeller-textiles.com/de/textilien/schoeller-wb-400 ; mass is calibrated. |

### Insulation and interiors

| Source ID | Product reference | Evidence used |
| --- | --- | --- |
| `PRODUCT_SOURCE_PRIMALOFT_GOLD_ACTIVE` | PrimaLoft Gold Insulation Active 60 | Official breathable four-way-stretch product family: https://primaloft.com/news/primaloft-inc-introduces-primaloft-gold-insulation-active-with-breathable-comfort-and-four-way-stretch-for-fall-2016/ ; grade behavior is calibrated as a shell surface. |
| `PRODUCT_SOURCE_3M_TAI1547` | 3M Thinsulate TAI1547 | Official 8 mm thickness, PP/PE web and 150 g/m² basis weight: https://www.3m.com/3M/en_US/p/d/b40068160/ |
| `PRODUCT_SOURCE_ALCANTARA_AUTOMOTIVE` | Alcantara Automotive | Official 68% polyester / 32% polyurethane composition and automotive use: https://www.alcantara.com/the-material/ and https://www.alcantara.com/applications/automotive/ ; mass is calibrated. |
| `PRODUCT_SOURCE_SUNBRELLA_SYSTEM_DUNE` | Sunbrella Sling System Dune 50198-0001 | Official woven composition and 15.33 oz/yd² weight: https://www.sunbrella.com/sunbrella-sling-system-dune-50198-0001 |

## Maintenance rules

New branded samples must:

1. use a stable `PRODUCT_SOURCE_*` identifier;
2. cite an official manufacturer page or clearly label a third-party commercial sample;
3. declare one of the supported data-quality levels;
4. identify all solver-only values as calibration rather than measurement;
5. avoid logos, imagery, proprietary datasets, binary assets and copied marketing text;
6. pass the atomic parser, solver-range and duplicate-ID tests.
