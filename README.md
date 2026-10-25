# Paragon Agro Limited - Production & Store Planning Portal (PWA & ERP Connected)

An enterprise-grade, multi-user web portal and mobile app planning system developed for **Paragon Agro Limited** (covering Dry Foods, Frozen Foods, Process Chicken, etc.).

---

## 🌟 Key Features & Updates

### 1. 📊 All Graphs with Real-Time Data Labels (সকল গ্রাফে ডেটা লেবেল)
- **Bar Chart (Forecast vs Plan vs Actual)**: প্রতিটি বারের উপরে নিখুঁত সংখ্যা (যেমন `1,250,000 Pkt`) বোল্ড টেক্সটে সরাসরি দেখা যায়। মাউস হোভার করা ছাড়াই যেকোনো ডিভাইস ও মোবাইল স্ক্রিনে এক নজরে সব ডেটা দৃশ্যমান।
- **Doughnut Chart (Stock Health Breakdown)**: প্রতিটি স্লাইসের ভেতরে সংখ্যা ও শতাংশ (যেমন `18 (64%)`, `6 (21%)`, `4 (15%)`) ক্রিস্প ড্রপ-শ্যাডো সহ স্বয়ংক্রিয়ভাবে ভেসে ওঠে।
- **Headroom & Theme Adaptation**: ডার্ক ও লাইট মোড পরিবর্তনের সাথে সাথে টেক্সট কালার স্বয়ংক্রিয়ভাবে অ্যাডজাস্ট হয় এবং গ্রাফের উপরে পর্যাপ্ত হেডরুম থাকায় কোনো সংখ্যা কেটে যায় না।

### 2. 📱 Mobile App (PWA) Support (মোবাইল অ্যাপ)
- সম্পূর্ণ **Progressive Web App (PWA)** সাপোর্টেড (`manifest.json` ও `sw.js` সংযুক্ত)।
- যেকোনো স্মার্টফোনে (Android/iPhone) ব্রাউজার থেকে **"Add to Home Screen"** অথবা **"Install App"** দিলেই এটি মোবাইল ফোনের ডিসপ্লেতে Paragon Plan অ্যাপের মতো আইকন নিয়ে সেভ হয়ে যাবে।
- অ্যাপ ওপেন করলে কোনো ব্রাউজার ইউআরএল বার বা কন্ট্রোল ছাড়া ফুল-স্ক্রিনে নেটিভ মোবাইল অ্যাপের অভিজ্ঞতা পাওয়া যাবে।

### 3. 📥 Bulk Uploads & Blank Templates
- **Sales Forecast Bulk Upload**: এক্সেল ফাইলের মাধ্যমে পুরো মাসের SKU-wise সেলস ফোরকাস্ট বাল্ক আপলোড।
- **Production Plan Template & Upload**: ১-ক্লিকে ব্ল্যাঙ্ক এক্সেল টেমপ্লেট ডাউনলোড করে টার্গেট এন্ট্রি করে আপলোড করলেই অটোমেটিক প্ল্যান ও স্টোর রিকুইজিশন তৈরি হয়।

### 4. ⚡ FSN Inventory Classification (Fast, Slow, Non-Moving)
- স্টোর ইনভেন্টরিতে সরাসরি **⚡ Fast Moving**, **🐢 Slow Moving**, এবং **💤 Non-Moving** কালার কোডেড ব্যাজ।
- স্টোর ফিল্টার বারে ১-ক্লিকে স্লো বা নন-মুভিং মেটেরিয়াল আলাদা করার ডেডিকেটেড ফিল্টার বাটন।

### 5. 📑 100% Solid Excel Cell Shading (ARGB Fix)
- প্রতিটি এক্সেল এক্সপোর্টে ৮-ডিজিট ARGB Hex (`FF1E3A8A`) ব্যবহারের ফলে ডেস্কটপ Excel ও Google Sheets-এ হেডার ও ডেটা সেলের ব্যাকগ্রাউন্ড শেডিং ১০০% উজ্জ্বল ও স্পষ্ট।
- বর্তমানে যে ট্যাবে থাকবেন, এক্সেল বাটনে ক্লিক করলে সেই ট্যাবেরই ডেডিকেটেড এক্সেল রিপোর্ট ডাউনলোড হবে।

### 6. 🔄 Multi-Revision Sales Forecast with Weekly Breakdown (ফোরকাস্ট সংশোধন ও ট্র্যাকিং)
- **Monthly Base & In-Month Revisions**: সেলস টিম মাসের শুরুতে প্রাথমিক ফোরকাস্ট (Rev 0) ইনপুট দেয়। মাসের যেকোনো সময়ে সাপ্তাহিক (W1, W2, W3, W4) বা নির্দিষ্ট সময়কালের জন্য ফোরকাস্ট একাধিকবার (Rev 1, Rev 2, Rev 3...) সংশোধন করতে পারে।
- **Audit Trail & Revision History Log**: প্রতিটি রিভিশনের তারিখ, পূর্বের সংখ্যা, সংশোধিত সংখ্যা, ভ্যারিয়েন্স (+/-) এবং সংশোধনের কারণ (যেমন: Week 3 Demand Surge) সিস্টেমে অডিট লগ হিসেবে সংরক্ষিত থাকে। টেবিলে `Rev #` ব্যাজে ক্লিক করলেই সম্পূর্ণ রিভিশন হিস্ট্রি দেখা যায়।

### 7. ⚡ Auto-Alignment of Daily Production Plans (প্রডাকশন অটো-রিঅ্যালাইনমেন্ট)
- সেলস ফোরকাস্ট রিভাইজড হওয়ার পর প্রডাকশন সেকশনে ১-ক্লিকে **"Auto-Align Daily Plans with Revised Forecast"** বাটন রয়েছে।
- রিভিশন তারিখ বা নির্দিষ্ট কার্যকারী তারিখ (Effective Date) থেকে ভবিষ্যতের দৈনিক প্ল্যানগুলোর টার্গেট স্বয়ংক্রিয়ভাবে পরিবর্তিত ফোরকাস্টের সাথে সামঞ্জস্যপূর্ণ হয়ে যায় এবং স্টোরের ম্যাটেরিয়াল রিকুইজিশনও নতুন টার্গেট অনুযায়ী পুনরায় হিসেব হয়।

### 8. 📅 Flexible Date & Range Forecast vs Production Reports (বহুমাত্রিক তুলনা রিপোর্ট)
- **Month Select**: নির্দিষ্ট মাসভিত্তিক (যেমন July 2026, August 2026) পূর্ণাঙ্গ রিপোর্ট।
- **Single Date Mode**: নির্দিষ্ট যেকোনো একক দিন (Specific Date) সিলেক্ট করে সেই দিনের প্ল্যান বনাম অর্জিত প্রডাকশন ও ফোরকাস্ট তুলনা।
- **Date Range Mode**: নির্দিষ্ট তারিখের পরিসর (যেমন ১ থেকে ১৫ তারিখ, Last 7 Days, Month-to-Date) অনুযায়ী ডায়নামিক ফিল্টারিং।
- রিপোর্টে মূল ফোরকাস্ট (Original), সংশোধিত ফোরকাস্ট (Active), রিভিশন নং, প্ল্যান টার্গেট, অ্যাকচুয়াল আউটপুট, ভ্যারিয়েন্স (+/-), অর্জনের শতকরা হার (%) এবং স্ট্যাটাস ব্যাজ (Exceeded, On Target, Shortfall) এক নজরে প্রদর্শিত হয়।

### 9. 🛡️ Super Admin Data Clean Tool
- সেকশন অনুযায়ী (Forecast, Production, MRR, Wastage, Requisitions, ইত্যাদি) নির্দিষ্ট ডেট রেঞ্জ অথবা অল ডাটা ক্লিয়ার করার নিরাপদ কন্ট্রোল প্যানেল।

---

## 🚀 GitHub ও Vercel-এ অনলাইন ডিপ্লয় করার নিয়ম (যেকোনো নেটওয়ার্ক থেকে ব্যবহারের জন্য)

`daily_update_portal`-এর মতো এই প্রজেক্টটিকেও খুব সহজে GitHub-এ পুশ করে Vercel-এ লাইভ করা যাবে:

### ধাপ ১: GitHub-এ আপলোড করুন
টার্মিনালে এই কমান্ডগুলো রান করুন:
```bash
cd "d:\AI project\Production & Store Plan"
git init
git add .
git commit -m "Production & Store Planning Portal with PWA and Vercel support"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo-name>.git
git push -u origin main
```
*(নোট: রিপোজিটরিটি **Private** রাখা নিরাপদ।)*

### ধাপ ২: Vercel-এ কানেক্ট ও ডিপ্লয় করুন
1. [vercel.com](https://vercel.com)-এ লগইন করে **"Add New Project"** সিলেক্ট করুন।
2. আপনার GitHub অ্যাকাউন্ট সিলেক্ট করে তৈরি করা রিপোজিটরিটি **Import** করুন।
3. **Framework Preset**: `Other` সিলেক্ট রাখুন (Vercel স্বয়ংক্রিয়ভাবে `vercel.json` এবং `requirements.txt` ডিটেক্ট করবে)।
4. **Deploy** বাটনে ক্লিক করুন। ২ মিনিটের মধ্যে আপনার লাইভ পাবলিক ইউআরএল তৈরি হয়ে যাবে (যেমন: `https://paragon-plan.vercel.app`)!

---

## 📱 মোবাইল ফোনে অ্যাপ হিসেবে ইনস্টল করার নিয়ম

1. **Android (Chrome)**:
   - মোবাইল ক্রোম ব্রাউজারে সাইটের লিংকে ঢুকুন।
   - উপরের ডানদিকের **থ্রি-ডট মেন্যু (⋮)**-তে ট্যাপ করুন।
   - **"Install App"** অথবা **"Add to Home Screen"** সিলেক্ট করুন।
2. **iPhone (Safari)**:
   - সাফারি ব্রাউজারে লিংকটি খুলুন।
   - নিচে মাঝের **Share আইকন** (বক্স ও তীরচিহ্ন ⎋)-এ ট্যাপ করুন।
   - নিচে স্ক্রোল করে **"Add to Home Screen"** দিন।

হোম স্ক্রিনে **Paragon Plan** আইকন তৈরি হবে এবং যেকোনো নেটওয়ার্ক (4G/Wi-Fi) থেকে সরাসরি ড্যাশবোর্ড দেখা যাবে!

---

## 💻 লোকাল মেশিনে চালানোর নিয়ম

PowerShell থেকে:
```powershell
python "d:\AI project\Production & Store Plan\portal\backend\app.py"
```
ব্রাউজারে ওপেন করুন: `http://localhost:5055`

### লগইন ক্রেডেনশিয়াল:
- **Super Admin**: `super_admin` / `admin123`
- **General Admin**: `admin` / `admin123`
