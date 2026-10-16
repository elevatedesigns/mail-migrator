# Perl dependencies for imapsync.
# Install with:  cpanm --installdeps .
# (or into a private folder:  cpanm -L local --installdeps .)

requires 'Authen::NTLM';
requires 'CGI';
requires 'Compress::Zlib';
requires 'Crypt::OpenSSL::RSA';
requires 'Data::Dumper';
requires 'Data::Uniqid';
requires 'Digest::HMAC';
requires 'Digest::HMAC_MD5';
requires 'Digest::HMAC_SHA1';
requires 'Digest::MD5';
requires 'Digest::SHA';
requires 'Email::Address';
requires 'Encode';
requires 'Encode::Byte';
requires 'Encode::IMAPUTF7';
requires 'File::Copy::Recursive';
requires 'File::Tail';
requires 'HTML::Entities';
requires 'HTTP::Daemon';
requires 'HTTP::Daemon::SSL';
requires 'HTTP::Request';
requires 'HTTP::Response';
requires 'HTTP::Status';
requires 'IO::Socket::INET';
requires 'IO::Socket::INET6';
requires 'IO::Socket::SSL';
requires 'IO::Tee';
requires 'JSON';
requires 'JSON::WebToken';
requires 'JSON::WebToken::Crypt::RSA';
requires 'LWP::UserAgent';
requires 'LWP::Protocol::https';
requires 'Mail::IMAPClient', '3.30';
requires 'MIME::Base64';
requires 'Module::Implementation';
requires 'Mozilla::CA';
requires 'Module::Runtime';
requires 'Module::ScanDeps';
requires 'Net::DNS';
requires 'Net::Server';
requires 'Net::SSLeay';
requires 'Package::Stash';
requires 'Package::Stash::XS';
requires 'Parse::RecDescent';
requires 'Pod::Usage';
requires 'Proc::ProcessTable';
requires 'Readonly';
requires 'Regexp::Common';
requires 'Sys::MemInfo';
requires 'Term::ReadKey';
requires 'Text::ParseWords';
requires 'Try::Tiny';
requires 'Unicode::String';
requires 'URI';

# Only needed for OAuth2 service-account authentication (--oauthaccesstoken with JSON/P12 keys).
recommends 'Crypt::OpenSSL::PKCS12';

on 'test' => sub {
    requires 'Test::Deep';
    requires 'Test::MockObject';
    requires 'Test::More';
    requires 'Test::Pod';
};

on 'develop' => sub {
    requires 'PAR::Packer';
};
