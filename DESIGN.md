# NataSekolah (FastAPI Core) — Design System & SSOT
**Version:** 3.0.0 (Vercel Geist Monochrome Edition)  
**Status:** Approved Standard  
**Maintainer:** Arsyad (@AarsyDesign) & Mizan  

---

## 1. Filosofi Visual (Vercel Aesthetic)
- **Monokromatik Tajam & Elegan:** Dominasi warna hitam `#000000`, putih `#ffffff`, dan abu-abu netral (`#fafafa` s/d `#171717`).
- **Data-Dense & Efisien:** Jarak padding terukur, tata letak tabel yang jelas, angka `tabular-nums`.
- **Panel Samping (Sidebar Navigation):** Navigasi sisi kiri terstruktur, collapsible di mobile, dengan item aktif berlatar halus dan teks tebal.
- **Micro-Interactions Taktil:** Feedback klik instan (100–150ms), tanpa efek membal liar (*no bouncy scale animations*).

---

## 2. Design Tokens
- Canvas: `#ffffff` (Dark mode: `#000000`)
- Surface: `#fafafa` (Dark mode: `#0a0a0a`)
- Surface Muted: `#f4f4f5` (Dark mode: `#141414`)
- Border: `#eaeaea` (Dark mode: `#222222`)
- Border Strong: `#d4d4d8` (Dark mode: `#333333`)
- Primary (Button/Active): `#000000` text `#ffffff`
- Text Main: `#171717` (Dark mode: `#ededed`)
- Text Muted: `#666666` (Dark mode: `#888888`)

### Semantic Status
- **Hadir (Present):** Emerald (`#10b981`, bg `#ecfdf5`, border `#a7f3d0`)
- **Terlambat (Late):** Amber (`#f59e0b`, bg `#fffbeb`, border `#fde68a`)
- **Izin (Excused):** Sky/Blue (`#0ea5e9`, bg `#f0f9ff`, border `#bae6fd`)
- **Sakit (Sick):** Indigo (`#6366f1`, bg `#eef2ff`, border `#c7d2fe`)
- **Alpa (Absent):** Rose (`#f43f5e`, bg `#fff1f2`, border `#fecdd3`)

---

## 3. Komponen Panel Samping (Sidebar)
- Lebar: `260px` di desktop, overlay drawer di mobile.
- Header: Logo NataSekolah + Label Lembaga + Status Online/Offline.
- Menu Items: Icon (Lucide SVG) + Label + Badge Counter (opsional).
- Active State: Background `#f4f4f5`, text `#000000`, font-medium, garis aksen kiri hitam.
- Bottom User Profile: Avatar inisial, nama guru/admin, role badge, dan tombol logout.
