import React, { useState } from 'react';
import { Link } from 'react-router-dom';

const Navbar = () => {
    const [isMenuOpen, setIsMenuOpen] = useState(false);

    return (
        <nav className="bg-[#f2f2f2] text-black h-24 font-montserrat border-b border-gray-200 relative z-[90]">
            <div className="container mx-auto px-6 h-full flex justify-between items-center">
                
                {/* Left: Logo */}
                <div className="w-1/4">
                    <Link to="/">
                        <span className="text-3xl font-marcellus tracking-widest text-[#1c2331]">PitchIQ</span>
                    </Link>
                </div>

                {/* Center: Links */}
                <div className="hidden md:flex flex-1 justify-center space-x-10 text-sm uppercase tracking-widest text-slate-500 font-medium">
                    <Link to="/" className="hover:text-[#1c2331] transition-colors">Home</Link>
                    <Link to="/chat" className="hover:text-[#1c2331] transition-colors">Agent</Link>
                    <Link to="/contact" className="hover:text-[#1c2331] transition-colors">Contact</Link>
                </div>

                {/* Right: Icons (Removed as requested, but keeping spacing balanced) */}
                <div className="hidden md:flex w-1/4 justify-end space-x-6 text-sm">
                   {/* Empty to balance flex layout */}
                </div>

                <div className="md:hidden flex items-center gap-4">
                    <button onClick={() => setIsMenuOpen(!isMenuOpen)} className="focus:outline-none z-50 relative text-black">
                        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={isMenuOpen ? "M6 18L18 6M6 6l12 12" : "M4 6h16M4 12h16M4 18h16"}></path>
                        </svg>
                    </button>
                </div>
            </div>

            <div className={`absolute top-0 left-0 w-full bg-white border-b border-gray-200 transform transition-transform duration-300 ease-in-out md:hidden ${isMenuOpen ? 'translate-x-0' : '-translate-x-full'} h-screen`}>
                <div className="flex flex-col space-y-4 p-6 text-sm uppercase tracking-widest pt-24 text-black">
                    <Link to="/" onClick={() => setIsMenuOpen(false)} className="hover:text-slate-500 py-2">Home</Link>
                    <Link to="/chat" onClick={() => setIsMenuOpen(false)} className="hover:text-slate-500 py-2">Agent</Link>
                    <Link to="/contact" onClick={() => setIsMenuOpen(false)} className="hover:text-slate-500 py-2">Contact</Link>
                </div>
            </div>
        </nav>
    );
};

export default Navbar;
