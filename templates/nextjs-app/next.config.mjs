/** @type {import('next').NextConfig} */
const nextConfig = {
    reactStrictMode: true,
    // Next.js 15 uses SWC by default - no need to configure
    images: {
        remotePatterns: [
            {
                protocol: 'https',
                hostname: 'images.unsplash.com',
            },
            {
                protocol: 'https',
                hostname: 'avatars.githubusercontent.com',
            },
        ],
    },
};

export default nextConfig;
