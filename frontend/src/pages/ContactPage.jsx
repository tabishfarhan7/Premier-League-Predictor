import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';

const ContactPage = () => {
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    organization: '',
    topic: 'match_prediction',
    fplTeamId: '',
    message: '',
  });
  const [isSubmitting, setIsSubmitting] = useState(false);

  const plStats = [
    { label: 'Clubs Analyzed', value: '20 / 20', desc: 'Every Premier League fixture covered' },
    { label: 'Fixtures Per Season', value: '380', desc: 'Pre-match and live statistical tracking' },
    { label: 'Historical Seasons', value: '10+', desc: 'Over 3,800 matches trained on XGBoost' },
    { label: 'Model Confidence', value: 'Calibrated', desc: 'Isotonic probabilities, not gut feelings' },
  ];

  const contactMethods = [
    {
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
        </svg>
      ),
      title: 'Analytical & General Inquiries',
      email: 'intel@pitchiq.ai',
      sub: 'Response within 24 hours on matchweeks',
    },
    {
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
        </svg>
      ),
      title: 'API & Data Partnerships',
      email: 'data-partners@pitchiq.ai',
      sub: 'Custom model feeds & live webhook feeds',
    },
    {
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9.5a2.5 2.5 0 00-2.5-2.5H15M9 11l3 3L22 4" />
        </svg>
      ),
      title: 'FPL & Fantasy Inquiries',
      email: 'fpl-desk@pitchiq.ai',
      sub: 'Squad optimization & captaincy queries',
    },
  ];

  const premierLeagueClubs = [
    'Arsenal', 'Aston Villa', 'Bournemouth', 'Brentford', 'Brighton', 'Chelsea',
    'Crystal Palace', 'Everton', 'Fulham', 'Ipswich Town', 'Leicester City', 'Liverpool',
    'Man City', 'Man United', 'Newcastle', 'Nottingham Forest', 'Southampton', 'Tottenham',
    'West Ham', 'Wolves'
  ];

  const handleChange = (e) => {
    setFormData((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    setIsSubmitting(true);
    setTimeout(() => {
      setIsSubmitting(false);
      toast.success('Your message has been received by the PitchIQ analyst team.');
      setFormData({
        name: '',
        email: '',
        organization: '',
        topic: 'match_prediction',
        fplTeamId: '',
        message: '',
      });
    }, 700);
  };

  return (
    <div className="bg-[#f2f2f2] text-slate-800 font-montserrat min-h-screen">
      {/* Hero Header */}
      <section className="container mx-auto text-center pt-20 md:pt-28 pb-14 px-6" data-aos="fade-up">
        <span className="text-xs uppercase tracking-widest text-slate-500 font-semibold mb-3 block">
          Connect With PitchIQ
        </span>
        <h1 className="font-marcellus text-4xl sm:text-5xl md:text-6xl text-[#1c2331] mb-6">
          Intelligence & Inquiry
        </h1>
        <p className="max-w-2xl mx-auto text-slate-600 text-base sm:text-lg leading-relaxed font-light">
          Whether you require bespoke Premier League predictive models, syndicated data pipelines, or high-tier Fantasy Premier League advisory, our team of sports data scientists is at your disposal.
        </p>
      </section>

      {/* Stats Counter Bar */}
      <section className="container mx-auto px-6 mb-16">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-6">
          {plStats.map((item, idx) => (
            <div
              key={idx}
              className="bg-white p-6 border border-gray-200 shadow-sm rounded-sm text-center hover:shadow-md transition-shadow"
            >
              <div className="font-marcellus text-3xl sm:text-4xl text-[#1c2331] mb-2">{item.value}</div>
              <div className="text-xs uppercase tracking-wider font-semibold text-slate-700 mb-1">{item.label}</div>
              <div className="text-xs text-slate-400 font-light">{item.desc}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Main Grid: PitchIQ & PL Information + Contact Form */}
      <section className="container mx-auto px-6 pb-24">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-12">
          {/* Left Column: Premier League & PitchIQ Details */}
          <div className="lg:col-span-5 space-y-10" data-aos="fade-right">
            {/* About PitchIQ */}
            <div className="bg-white p-8 border border-gray-200 shadow-sm rounded-sm">
              <span className="text-xs uppercase tracking-widest text-slate-400 font-semibold mb-2 block">
                The PitchIQ Architecture
              </span>
              <h2 className="font-marcellus text-2xl text-[#1c2331] mb-4">
                Quantitative Football Analysis
              </h2>
              <p className="text-slate-600 text-sm leading-relaxed font-light mb-4">
                PitchIQ eliminates speculative football punditry. Our proprietary pipeline combines dynamic Elo rating engines, rolling form calculations, home/away split metrics, and calibrated gradient-boosted trees (XGBoost).
              </p>
              <p className="text-slate-600 text-sm leading-relaxed font-light mb-6">
                Integrated directly with official Fantasy Premier League (FPL) availability matrices and verified team news, PitchIQ provides precision probabilistic forecasting for every Premier League matchweek.
              </p>

              <div className="border-t border-gray-100 pt-6">
                <h3 className="font-marcellus text-base text-[#1c2331] mb-3">Covered Premier League Clubs</h3>
                <div className="flex flex-wrap gap-1.5">
                  {premierLeagueClubs.map((club) => (
                    <span
                      key={club}
                      className="text-[11px] bg-[#f2f2f2] text-slate-700 px-2.5 py-1 rounded-sm border border-gray-200/60 font-medium"
                    >
                      {club}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* Direct Communication Channels */}
            <div className="bg-white p-8 border border-gray-200 shadow-sm rounded-sm space-y-6">
              <h2 className="font-marcellus text-xl text-[#1c2331]">Direct Desks</h2>
              {contactMethods.map((cm, idx) => (
                <div key={idx} className="flex items-start space-x-4 group">
                  <div className="w-10 h-10 rounded-full bg-[#f2f2f2] flex items-center justify-center text-[#1c2331] group-hover:bg-[#1c2331] group-hover:text-white transition-colors duration-300 flex-shrink-0">
                    {cm.icon}
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">{cm.title}</h3>
                    <a
                      href={`mailto:${cm.email}`}
                      className="text-sm text-[#1c2331] font-medium hover:underline block"
                    >
                      {cm.email}
                    </a>
                    <p className="text-xs text-slate-400 font-light mt-0.5">{cm.sub}</p>
                  </div>
                </div>
              ))}
            </div>

            {/* Matchday Operations Notice */}
            <div className="bg-[#1c2331] text-white p-8 rounded-sm shadow-md">
              <span className="text-[11px] tracking-widest uppercase text-emerald-400 font-semibold mb-2 block">
                Live Status
              </span>
              <h3 className="font-marcellus text-xl mb-2">Matchday Data Ops</h3>
              <p className="text-slate-300 text-xs leading-relaxed font-light">
                Model training occurs continuously throughout the Premier League season. ETL pipelines ingest verified match data post-whistle, updating league ratings and training sets every Monday at 02:00 UTC.
              </p>
            </div>
          </div>

          {/* Right Column: Contact & Advisory Form */}
          <div className="lg:col-span-7" data-aos="fade-left">
            <div className="bg-white p-8 md:p-12 border border-gray-200 shadow-sm rounded-sm">
              <div className="mb-8">
                <span className="text-xs uppercase tracking-widest text-slate-400 font-semibold mb-2 block">
                  Communication
                </span>
                <h2 className="font-marcellus text-3xl text-[#1c2331] mb-3">Send a Dispatch</h2>
                <p className="text-slate-500 text-sm font-light leading-relaxed">
                  Have questions about our Premier League model weights, FPL captaincy recommendations, or enterprise API access? Submit your inquiry below.
                </p>
              </div>

              <form onSubmit={handleSubmit} className="space-y-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <label htmlFor="name" className="block text-xs uppercase tracking-wider font-semibold text-slate-700 mb-2">
                      Full Name *
                    </label>
                    <input
                      id="name"
                      type="text"
                      name="name"
                      required
                      value={formData.name}
                      onChange={handleChange}
                      placeholder="e.g. Tabish Farhan"
                      className="w-full bg-[#f8f9fa] border border-gray-300 px-4 py-3 text-sm rounded-sm focus:outline-none focus:border-[#1c2331] focus:bg-white transition-colors"
                    />
                  </div>
                  <div>
                    <label htmlFor="email" className="block text-xs uppercase tracking-wider font-semibold text-slate-700 mb-2">
                      Email Address *
                    </label>
                    <input
                      id="email"
                      type="email"
                      name="email"
                      required
                      value={formData.email}
                      onChange={handleChange}
                      placeholder="analyst@domain.com"
                      className="w-full bg-[#f8f9fa] border border-gray-300 px-4 py-3 text-sm rounded-sm focus:outline-none focus:border-[#1c2331] focus:bg-white transition-colors"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <label htmlFor="topic" className="block text-xs uppercase tracking-wider font-semibold text-slate-700 mb-2">
                      Inquiry Category *
                    </label>
                    <select
                      id="topic"
                      name="topic"
                      value={formData.topic}
                      onChange={handleChange}
                      className="w-full bg-[#f8f9fa] border border-gray-300 px-4 py-3 text-sm rounded-sm focus:outline-none focus:border-[#1c2331] focus:bg-white transition-colors text-slate-800"
                    >
                      <option value="match_prediction">Match Prediction & XGBoost Model</option>
                      <option value="fpl_strategy">Fantasy Premier League (FPL) Advisory</option>
                      <option value="api_access">Institutional API & Data Feed</option>
                      <option value="feature_request">Feature Request & Feedback</option>
                      <option value="press">Media & Press</option>
                    </select>
                  </div>
                  <div>
                    <label htmlFor="organization" className="block text-xs uppercase tracking-wider font-semibold text-slate-700 mb-2">
                      Club / Organization (Optional)
                    </label>
                    <input
                      id="organization"
                      type="text"
                      name="organization"
                      value={formData.organization}
                      onChange={handleChange}
                      placeholder="e.g. Analytics Syndicate"
                      className="w-full bg-[#f8f9fa] border border-gray-300 px-4 py-3 text-sm rounded-sm focus:outline-none focus:border-[#1c2331] focus:bg-white transition-colors"
                    />
                  </div>
                </div>

                {formData.topic === 'fpl_strategy' && (
                  <div>
                    <label htmlFor="fplTeamId" className="block text-xs uppercase tracking-wider font-semibold text-slate-700 mb-2">
                      FPL Team ID (Optional for custom audit)
                    </label>
                    <input
                      id="fplTeamId"
                      type="text"
                      name="fplTeamId"
                      value={formData.fplTeamId}
                      onChange={handleChange}
                      placeholder="e.g. 142095"
                      className="w-full bg-[#f8f9fa] border border-gray-300 px-4 py-3 text-sm rounded-sm focus:outline-none focus:border-[#1c2331] focus:bg-white transition-colors"
                    />
                  </div>
                )}

                <div>
                  <label htmlFor="message" className="block text-xs uppercase tracking-wider font-semibold text-slate-700 mb-2">
                    Message / Analytical Query *
                  </label>
                  <textarea
                    id="message"
                    name="message"
                    required
                    rows={6}
                    value={formData.message}
                    onChange={handleChange}
                    placeholder="Specify the match, statistical variable, or integration requirements you wish to discuss..."
                    className="w-full bg-[#f8f9fa] border border-gray-300 px-4 py-3 text-sm rounded-sm focus:outline-none focus:border-[#1c2331] focus:bg-white transition-colors resize-y"
                  ></textarea>
                </div>

                <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-2">
                  <p className="text-xs text-slate-400 font-light">
                    Protected by PitchIQ encryption. We never share proprietary strategy details.
                  </p>
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="w-full sm:w-auto bg-[#1c2331] text-white px-10 py-3.5 text-xs font-semibold uppercase tracking-widest hover:bg-black transition-colors rounded-sm shadow-sm disabled:opacity-50"
                  >
                    {isSubmitting ? 'Transmitting...' : 'Dispatch Message'}
                  </button>
                </div>
              </form>
            </div>

            {/* Quick Link Card to Agent */}
            <div className="mt-8 bg-white border border-gray-200 p-6 rounded-sm flex flex-col sm:flex-row items-center justify-between gap-4">
              <div>
                <h4 className="font-marcellus text-lg text-[#1c2331]">Need Immediate Match Analysis?</h4>
                <p className="text-xs text-slate-500 font-light">
                  Query the interactive PitchIQ Agent for instant calibrated win probabilities and tactical breakdowns.
                </p>
              </div>
              <Link
                to="/agent"
                className="inline-block border border-[#1c2331] text-[#1c2331] px-6 py-2.5 text-xs uppercase tracking-widest hover:bg-[#1c2331] hover:text-white transition-all whitespace-nowrap"
              >
                Consult Analyst Console &rarr;
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
};

export default ContactPage;
