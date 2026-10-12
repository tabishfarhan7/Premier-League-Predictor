import React, { useRef, useState, useEffect } from 'react';
import { Link } from 'react-router-dom';

import bannerImg from '../assets/banner.png';
import introVideo from '../assets/intro.mp4';
import bgAudioFile from "../assets/Khaled - C'Est La Vie - Khaled (128k).mp3";
import secondAudioFile from "../assets/videoplayback.weba";

// Import images 1 to 11
import img1 from '../assets/1.jpg';
import img2 from '../assets/2.jpg';
import img3 from '../assets/3.jpg';
import img4 from '../assets/4.jpg';
import img5 from '../assets/5.jpg';
import img6 from '../assets/6.jpg';
import img7 from '../assets/7.jpg';
import img8 from '../assets/8.jpg';
import img9 from '../assets/9.jpg';
import img10 from '../assets/10.jpg';
import img11 from '../assets/11.jpg';
import img12 from '../assets/12.jpg';
import img13 from '../assets/13.jpg';
import img14 from '../assets/14.jpg';
import img15 from '../assets/15.jpg';

const carouselImages = [img2, img3, img4, img5, img6, img7, img8, img9, img10, img11, img12, img13, img14, img15];

// Dynamically discover all audio files added to assets (mp3, wav, ogg, m4a, aac, weba, webm, webp)
const audioGlob = import.meta.glob('../assets/*.{mp3,wav,ogg,m4a,aac,weba,webm,webp}', { eager: true, import: 'default' });
const extraAudioEntries = Object.entries(audioGlob)
  .filter(([path]) => !path.includes("Khaled") && !path.includes("videoplayback"))
  .map(([path, url]) => {
    const filename = path.split('/').pop().replace(/\.[^/.]+$/, '');
    return { title: filename, src: url };
  });

// Ordered playlist: Khaled first, videoplayback second, then any additional tracks
const playlist = [
  { title: "C'Est La Vie - Khaled", src: bgAudioFile },
  { title: "Videoplayback", src: secondAudioFile },
  ...extraAudioEntries,
];

const HomePage = () => {
  // Only play intro video once when the website is opened
  const [introState, setIntroState] = useState(() => {
    return sessionStorage.getItem('pitchiq_intro_seen') ? 'home' : 'banner';
  });
  const [isCategoryPaused, setCategoryPaused] = useState(false);

  const finishIntro = () => {
    sessionStorage.setItem('pitchiq_intro_seen', 'true');
    setIntroState('home');
  };

  const videoRef = useRef(null);
  const [isMuted, setIsMuted] = useState(false);

  // Background soothing audio playlist
  const bgAudioRef = useRef(null);
  const [currentTrackIndex, setCurrentTrackIndex] = useState(0);
  const [isBgPlaying, setIsBgPlaying] = useState(false);

  const playBgTrack = () => {
    if (bgAudioRef.current) {
      bgAudioRef.current.volume = 0.18; // soothing low volume
      const playPromise = bgAudioRef.current.play();
      if (playPromise !== undefined) {
        playPromise
          .then(() => {
            setIsBgPlaying(true);
          })
          .catch(() => {
            // Unmute & play on first user interaction if blocked
            const onUserActivation = () => {
              if (bgAudioRef.current) {
                bgAudioRef.current.volume = 0.18;
                bgAudioRef.current.play().then(() => setIsBgPlaying(true)).catch(() => {});
              }
              ['pointerdown', 'click', 'keydown', 'touchstart'].forEach((evt) => {
                window.removeEventListener(evt, onUserActivation);
              });
            };
            ['pointerdown', 'click', 'keydown', 'touchstart'].forEach((evt) => {
              window.addEventListener(evt, onUserActivation, { once: true });
            });
          });
      }
    }
  };

  useEffect(() => {
    if (introState === 'banner') {
      const timer = setTimeout(() => setIntroState('video'), 2000);
      return () => clearTimeout(timer);
    } else if (introState === 'video' && videoRef.current) {
      const video = videoRef.current;
      video.muted = false;
      video.volume = 1.0;
      const playPromise = video.play();
      if (playPromise !== undefined) {
        playPromise
          .then(() => {
            setIsMuted(false);
          })
          .catch(() => {
            video.muted = true;
            setIsMuted(true);
            video.play();

            const unmuteOnUserGesture = () => {
              if (videoRef.current) {
                videoRef.current.muted = false;
                setIsMuted(false);
              }
              window.removeEventListener('click', unmuteOnUserGesture);
              window.removeEventListener('touchstart', unmuteOnUserGesture);
            };
            window.addEventListener('click', unmuteOnUserGesture, { once: true });
            window.addEventListener('touchstart', unmuteOnUserGesture, { once: true });
          });
      }
    } else if (introState === 'home') {
      if (videoRef.current) {
        videoRef.current.pause();
      }
      playBgTrack();
    }
  }, [introState]);

  // When track index changes while in 'home', play the next track immediately
  useEffect(() => {
    if (introState === 'home' && bgAudioRef.current) {
      bgAudioRef.current.src = playlist[currentTrackIndex].src;
      bgAudioRef.current.currentTime = 0;
      playBgTrack();
    }
  }, [currentTrackIndex, introState]);

  const handleTrackEnded = () => {
    // Continuous loop over playlist
    setCurrentTrackIndex((prev) => (prev + 1) % playlist.length);
  };

  const nextTrack = () => {
    setCurrentTrackIndex((prev) => (prev + 1) % playlist.length);
  };

  const prevTrack = () => {
    setCurrentTrackIndex((prev) => (prev - 1 + playlist.length) % playlist.length);
  };

  const toggleBgAudio = () => {
    if (bgAudioRef.current) {
      if (isBgPlaying) {
        bgAudioRef.current.pause();
        setIsBgPlaying(false);
      } else {
        bgAudioRef.current.volume = 0.18;
        bgAudioRef.current.play().then(() => setIsBgPlaying(true)).catch(() => {});
      }
    }
  };

  const handleBannerClick = () => {
    // Warm up audio context on user gesture
    if (bgAudioRef.current) {
      bgAudioRef.current.volume = 0;
      bgAudioRef.current.play().then(() => {
        bgAudioRef.current.pause();
        bgAudioRef.current.currentTime = 0;
      }).catch(() => {});
    }
    setIntroState('video');
  };

  const toggleSound = () => {
    if (videoRef.current) {
      videoRef.current.muted = !videoRef.current.muted;
      setIsMuted(videoRef.current.muted);
    }
  };

  if (introState === 'banner') {
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-white p-8 md:p-24">
        {/* Hidden audio element primed early */}
        <audio
          ref={bgAudioRef}
          src={playlist[currentTrackIndex]?.src}
          onEnded={handleTrackEnded}
          preload="auto"
        />
        <div
          className="relative w-full h-full max-w-6xl bg-white flex flex-col items-center justify-center border-4 border-white shadow-[0_0_40px_rgba(0,0,0,0.08)] rounded-3xl overflow-hidden cursor-pointer group"
          onClick={handleBannerClick}
        >
          <img src={bannerImg} alt="Banner" className="w-full h-full object-contain p-4 group-hover:scale-[1.02] transition-transform duration-700" />
          <p className="absolute bottom-8 text-xs text-slate-400 font-montserrat uppercase tracking-widest animate-pulse">Click anywhere to enter with Audio</p>
        </div>
      </div>
    );
  }

  if (introState === 'video') {
    return (
      <div className="fixed inset-0 z-[100] bg-black flex flex-col items-center justify-center group">
        <video
          ref={videoRef}
          id="intro-video-player"
          src={introVideo}
          className="w-full h-full object-cover"
          autoPlay
          playsInline
          onEnded={finishIntro}
        />
        <button
          onClick={toggleSound}
          className="absolute bottom-32 left-1/2 -translate-x-1/2 text-white/80 hover:text-white transition-colors z-[101] flex items-center space-x-2 text-sm uppercase tracking-widest bg-black/40 px-4 py-2 rounded-full backdrop-blur-md border border-white/20"
        >
          {isMuted ? (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M9.383 3.076A1 1 0 0110 4v12a1 1 0 01-1.707.707L4.586 13H2a1 1 0 01-1-1V8a1 1 0 011-1h2.586l3.707-3.707a1 1 0 011.09-.217zM12.293 7.293a1 1 0 011.414 0L15 8.586l1.293-1.293a1 1 0 111.414 1.414L16.414 10l1.293 1.293a1 1 0 01-1.414 1.414L15 11.414l-1.293 1.293a1 1 0 01-1.414-1.414L13.586 10l-1.293-1.293a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-emerald-400" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M9.383 3.076A1 1 0 0110 4v12a1 1 0 01-1.707.707L4.586 13H2a1 1 0 01-1-1V8a1 1 0 011-1h2.586l3.707-3.707a1 1 0 011.09-.217zM14.657 2.929a1 1 0 011.414 0A9.972 9.972 0 0119 10a9.972 9.972 0 01-2.929 7.071 1 1 0 01-1.414-1.414A7.971 7.971 0 0017 10c0-2.21-.894-4.208-2.343-5.657a1 1 0 010-1.414zm-2.829 2.828a1 1 0 011.415 0A5.983 5.983 0 0115 10a5.984 5.984 0 01-1.757 4.243 1 1 0 01-1.415-1.415A3.984 3.984 0 0013 10a3.983 3.983 0 00-1.172-2.828 1 1 0 010-1.415z" clipRule="evenodd" />
            </svg>
          )}
          <span>{isMuted ? 'Sound Off (Click to Play Audio)' : 'Audio Playing'}</span>
        </button>
        <button
          onClick={finishIntro}
          className="absolute bottom-12 left-1/2 -translate-x-1/2 text-white border border-white/40 px-10 py-4 hover:bg-white hover:text-black transition-all z-[101] tracking-widest uppercase text-xs font-semibold backdrop-blur-sm"
        >
          Skip Intro
        </button>
      </div>
    );
  }

  return (
    <div className="HomePageWrapper bg-[#f2f2f2] text-slate-800 font-montserrat overflow-x-hidden min-h-screen">

      {/* Hero Section */}
      <div className="container mx-auto text-center pt-24 md:pt-32 pb-16 px-4 bg-[#f2f2f2]" data-aos="fade-up">
        <h1 className="font-marcellus text-5xl sm:text-6xl md:text-7xl mb-6 text-[#1c2331]">PitchIQ</h1>
        <p className="max-w-xl sm:max-w-2xl mx-auto text-slate-600 text-base sm:text-lg leading-relaxed font-light">
          With cutting-edge XGBoost models and real-time news parsing, we create more than just predictions; we tailor intelligence. Each insight is a testament to our legacy of data quality and precision, ensuring you outsmart the game.
        </p>
      </div>

      {/* Continuous Marquee Carousel (1 to 15 images) */}
      <div className="relative mb-24 mt-8 overflow-hidden" data-aos="fade-up">

        <div
          className={`flex w-[200%] space-x-6 px-4 animate-marquee ${isCategoryPaused ? 'play-state-paused' : 'play-state-running'}`}
          onMouseEnter={() => setCategoryPaused(true)}
          onMouseLeave={() => setCategoryPaused(false)}
        >
          {/* Double the array for seamless looping */}
          {[...carouselImages, ...carouselImages].map((image, index) => (
            <div key={index} className="group flex-shrink-0 w-80 text-left cursor-pointer">
              <div className="overflow-hidden bg-white shadow-sm hover:shadow-xl transition-shadow duration-500 rounded-sm">
                <img
                  src={image}
                  alt={`PitchIQ Showcase ${index + 1}`}
                  className="w-full h-[36rem] object-cover group-hover:scale-[1.03] transition-transform duration-700 ease-in-out"
                />
              </div>
            </div>
          ))}
        </div>

      </div>

      {/* Distinctive Feature Grid */}
      <div className="bg-white py-24 border-y border-gray-200">
        <div className="container mx-auto px-6">
          <div className="text-center mb-16 max-w-3xl mx-auto">
            <h2 className="font-marcellus text-3xl md:text-4xl text-[#1c2331] mb-6">The Anatomy of Precision</h2>
            <p className="text-slate-500 leading-relaxed font-light">We break down the chaotic nature of the Premier League into actionable, structured data. Experience analysis crafted with the exactitude of a master tailor.</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-12">
            {/* Feature 1 */}
            <div className="text-center group">
              <div className="w-16 h-16 mx-auto bg-[#f2f2f2] rounded-full flex items-center justify-center mb-6 group-hover:bg-[#1c2331] transition-colors duration-500">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-[#1c2331] group-hover:text-white transition-colors duration-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
              </div>
              <h3 className="font-marcellus text-xl mb-3 text-[#1c2331]">XGBoost Architecture</h3>
              <p className="text-slate-500 text-sm leading-relaxed font-light">Trained on thousands of historical matches, our proprietary models identify invisible patterns and momentum shifts.</p>
            </div>

            {/* Feature 2 */}
            <div className="text-center group">
              <div className="w-16 h-16 mx-auto bg-[#f2f2f2] rounded-full flex items-center justify-center mb-6 group-hover:bg-[#1c2331] transition-colors duration-500">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-[#1c2331] group-hover:text-white transition-colors duration-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9.5a2.5 2.5 0 00-2.5-2.5H15M9 11l3 3L22 4" /></svg>
              </div>
              <h3 className="font-marcellus text-xl mb-3 text-[#1c2331]">FPL Optimization</h3>
              <p className="text-slate-500 text-sm leading-relaxed font-light">Move past gut feelings. Optimize your squad with expected points (xP), fixture difficulty ratings, and injury timelines.</p>
            </div>

            {/* Feature 3 */}
            <div className="text-center group">
              <div className="w-16 h-16 mx-auto bg-[#f2f2f2] rounded-full flex items-center justify-center mb-6 group-hover:bg-[#1c2331] transition-colors duration-500">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-[#1c2331] group-hover:text-white transition-colors duration-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" /></svg>
              </div>
              <h3 className="font-marcellus text-xl mb-3 text-[#1c2331]">Live Conversational Agent</h3>
              <p className="text-slate-500 text-sm leading-relaxed font-light">Consult our AI just like a personal analyst. Ask about team forms, predicted lineups, or direct head-to-head records.</p>
            </div>
          </div>
        </div>
      </div>

      {/* Split Content Section */}
      <div className="container mx-auto px-6 py-24">
        <div className="flex flex-col md:flex-row items-center gap-16">
          <div className="w-full md:w-1/2">
            <div className="overflow-hidden rounded-sm shadow-xl">
              <img src={img1} alt="Analytics Dashboard" className="w-full h-[400px] object-cover hover:scale-105 transition-transform duration-1000" />
            </div>
          </div>
          <div className="w-full md:w-1/2">
            <span className="text-xs uppercase tracking-widest text-slate-400 font-semibold mb-4 block">The PitchIQ Standard</span>
            <h2 className="font-marcellus text-4xl text-[#1c2331] mb-6 leading-snug">Redefining Match Intelligence</h2>
            <p className="text-slate-500 mb-6 leading-relaxed font-light">
              We believe that true insight lies beyond the surface metrics. By analyzing underlying performance data, expected goals (xG), and spatial control, PitchIQ uncovers the hidden narratives of every matchweek.
            </p>
            <p className="text-slate-500 mb-8 leading-relaxed font-light">
              Whether you are finalizing your fantasy transfers or previewing the weekend's biggest derby, our platform provides the bespoke intelligence you need to make the definitive call.
            </p>
            <Link to="/chat" className="inline-block border border-[#1c2331] text-[#1c2331] px-10 py-3 text-sm uppercase tracking-widest hover:bg-[#1c2331] hover:text-white transition-all duration-300">
              Speak to the Agent
            </Link>
          </div>
        </div>
      </div>

      {/* Hidden background audio element with auto-advance and continuous loop */}
      <audio
        ref={bgAudioRef}
        src={playlist[currentTrackIndex]?.src}
        onEnded={handleTrackEnded}
        preload="auto"
      />

      {/* Floating Ambient Music Controller with Playlist support */}
      <div className="fixed bottom-6 right-6 z-50 flex items-center space-x-1.5 bg-white/95 backdrop-blur-md px-3 py-2 rounded-full shadow-[0_4px_24px_rgba(0,0,0,0.14)] border border-gray-200/90 text-[#1c2331]">
        {playlist.length > 1 && (
          <button
            onClick={prevTrack}
            className="p-1 hover:bg-gray-100 rounded-full transition-colors text-slate-500 hover:text-[#1c2331]"
            title="Previous track"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
        )}

        <button
          onClick={toggleBgAudio}
          className="flex items-center space-x-2 text-xs uppercase tracking-wider font-medium group cursor-pointer px-1.5 py-0.5"
          title="Toggle soothing background music"
        >
          <div className="flex items-end space-x-0.5 h-3.5 w-3.5">
            <span className={`w-0.5 bg-[#1c2331] rounded-full transition-all duration-300 ${isBgPlaying ? 'h-3 animate-pulse' : 'h-1'}`}></span>
            <span className={`w-0.5 bg-[#1c2331] rounded-full transition-all duration-300 ${isBgPlaying ? 'h-2 animate-bounce' : 'h-1'}`}></span>
            <span className={`w-0.5 bg-[#1c2331] rounded-full transition-all duration-300 ${isBgPlaying ? 'h-3.5 animate-pulse' : 'h-1'}`}></span>
          </div>
          <span className="text-[11px] font-semibold text-slate-800">
            {isBgPlaying ? 'Playing' : 'Paused'}
          </span>
          <span className="text-[10px] text-slate-500 max-w-[130px] truncate hidden sm:inline">
            {playlist[currentTrackIndex]?.title || 'Ambient Music'}
          </span>
          {playlist.length > 1 && (
            <span className="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded-full text-slate-600 font-mono">
              {currentTrackIndex + 1}/{playlist.length}
            </span>
          )}
        </button>

        {playlist.length > 1 && (
          <button
            onClick={nextTrack}
            className="p-1 hover:bg-gray-100 rounded-full transition-colors text-slate-500 hover:text-[#1c2331]"
            title="Next track"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        )}
      </div>

    </div>
  );
};

export default HomePage;
